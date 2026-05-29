# ============================================================
#  AGENT MANAGER — Background Sub-Agent Lifecycle
# ============================================================
#  Manages background agents (agy tasks) that run concurrently.
#
#  Each agent tracks:
#  - A short auto-generated title
#  - Status (running / done / failed / stopped)
#  - Elapsed time, result text, error messages
#
#  State is persisted to a JSON file so it survives restarts.
#  On reload, any previously-running agents are marked 'stopped'
#  because their asyncio tasks died with the process.
# ============================================================

import json
import os
import logging
import asyncio
from datetime import datetime
from typing import Optional

from config import AGENTS_FILE, MAX_CONCURRENT_AGENTS, BRAIN_DIR

logger = logging.getLogger(__name__)

# ── Datetime format used for JSON serialization ─────────────
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


# ============================================================
#  AgentInfo — Data class for a single agent
# ============================================================

class AgentInfo:
    """
    Holds all metadata for a single background agent.
    Fully serializable to/from JSON via to_dict / from_dict.
    """

    def __init__(
        self,
        agent_id: str,
        task: str,
        conversation_id: str | None = None,
    ):
        self.id: str = agent_id
        self.task: str = task
        self.title: str = self._generate_title(task)
        self.status: str = "running"  # running | done | failed | stopped
        self.conversation_id: str | None = conversation_id
        self.started_at: datetime = datetime.now()
        self.finished_at: datetime | None = None
        self.result: str | None = None
        self.error: str | None = None

    # ── Title generation ────────────────────────────────────

    @staticmethod
    def _generate_title(task: str) -> str:
        """Create a short human-readable title from the task description."""
        words = task.split()[:7]
        title = " ".join(words)
        if len(title) > 40:
            title = title[:37] + "..."
        return title

    # ── Elapsed time ────────────────────────────────────────

    def elapsed_str(self) -> str:
        """Return a human-readable elapsed time string (e.g. '2m 34s')."""
        end = self.finished_at or datetime.now()
        delta = end - self.started_at
        total_seconds = int(delta.total_seconds())

        if total_seconds < 0:
            return "0s"

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    # ── Serialization ───────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize this agent to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "task": self.task,
            "title": self.title,
            "status": self.status,
            "conversation_id": self.conversation_id,
            "started_at": self.started_at.strftime(_DATETIME_FMT),
            "finished_at": (
                self.finished_at.strftime(_DATETIME_FMT)
                if self.finished_at
                else None
            ),
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentInfo":
        """Reconstruct an AgentInfo from a dictionary (loaded from JSON)."""
        agent = cls.__new__(cls)
        agent.id = data["id"]
        agent.task = data.get("task", "")
        agent.title = data.get("title", cls._generate_title(agent.task))
        agent.status = data.get("status", "stopped")
        agent.conversation_id = data.get("conversation_id")
        agent.started_at = datetime.strptime(data["started_at"], _DATETIME_FMT)
        agent.finished_at = (
            datetime.strptime(data["finished_at"], _DATETIME_FMT)
            if data.get("finished_at")
            else None
        )
        agent.result = data.get("result")
        agent.error = data.get("error")
        return agent

    # ── repr ────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<AgentInfo {self.id} [{self.status}] {self.title!r}>"


# ============================================================
#  AgentManager — Orchestrates all background agents
# ============================================================

class AgentManager:
    """
    Creates, tracks, and persists background agents.

    State is saved to a JSON file so agent history survives restarts.
    The _running_tasks dict holds live asyncio.Task references and
    is NOT persisted — it is rebuilt at runtime.
    """

    def __init__(
        self,
        agents_file: str = AGENTS_FILE,
        max_agents: int = MAX_CONCURRENT_AGENTS,
    ):
        self.agents_file: str = agents_file
        self.max_agents: int = max_agents
        self.agents: dict[str, AgentInfo] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._load()

    # ════════════════════════════════════════════════════════
    #  Persistence
    # ════════════════════════════════════════════════════════

    def _load(self) -> None:
        """
        Load agent state from the JSON file.

        Any agents that were 'running' when the bot last stopped are
        marked 'stopped' — their asyncio tasks no longer exist.
        """
        if not os.path.exists(self.agents_file):
            logger.info("[AGENTS] No agents file found, starting fresh.")
            return

        try:
            with open(self.agents_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            stale_count = 0
            for entry in data.get("agents", []):
                agent = AgentInfo.from_dict(entry)
                # Mark orphaned running agents as stopped
                if agent.status == "running":
                    agent.status = "stopped"
                    agent.finished_at = datetime.now()
                    agent.error = "Bot restarted — agent was terminated."
                    stale_count += 1
                self.agents[agent.id] = agent

            if stale_count:
                logger.warning(
                    f"[AGENTS] Marked {stale_count} stale agent(s) as stopped."
                )
                self._save()

            logger.info(
                f"[AGENTS] Loaded {len(self.agents)} agent(s) from disk."
            )

        except Exception as e:
            logger.error(f"[AGENTS] Failed to load agents file: {e}")
            self.agents = {}

    def _save(self) -> None:
        """Persist the current agent state to the JSON file."""
        try:
            data = {
                "agents": [a.to_dict() for a in self.agents.values()],
            }
            with open(self.agents_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[AGENTS] Failed to save agents file: {e}")

    # ════════════════════════════════════════════════════════
    #  ID Generation
    # ════════════════════════════════════════════════════════

    def _next_id(self) -> str:
        """
        Generate the next sequential agent ID.
        Scans existing IDs to find the highest number, then increments.
        """
        max_num = 0
        for aid in self.agents:
            # Expected format: 'agent-<number>'
            try:
                num = int(aid.split("-", 1)[1])
                max_num = max(max_num, num)
            except (IndexError, ValueError):
                continue
        return f"agent-{max_num + 1}"

    # ════════════════════════════════════════════════════════
    #  Agent Lifecycle
    # ════════════════════════════════════════════════════════

    def launch(self, task: str) -> AgentInfo | None:
        """
        Create and register a new agent for the given task.

        Returns the AgentInfo on success, or None if the maximum
        number of concurrent running agents has been reached.
        """
        running = self.list_running()
        if len(running) >= self.max_agents:
            logger.warning(
                f"[AGENTS] Cannot launch — already at max capacity "
                f"({self.max_agents} running)."
            )
            return None

        agent_id = self._next_id()
        agent = AgentInfo(agent_id=agent_id, task=task)
        self.agents[agent_id] = agent
        self._save()

        logger.info(f"[AGENTS] Launched {agent_id}: {agent.title!r}")
        return agent

    def get(self, agent_id: str) -> AgentInfo | None:
        """Retrieve an agent by its ID, or None if not found."""
        return self.agents.get(agent_id)

    def complete(self, agent_id: str, result: str) -> None:
        """Mark an agent as successfully completed with its result."""
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning(f"[AGENTS] complete() called for unknown {agent_id}")
            return

        agent.status = "done"
        agent.result = result
        agent.finished_at = datetime.now()
        self._running_tasks.pop(agent_id, None)
        self._save()

        logger.info(
            f"[AGENTS] {agent_id} completed in {agent.elapsed_str()}."
        )

    def fail(self, agent_id: str, error: str) -> None:
        """Mark an agent as failed with an error message."""
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning(f"[AGENTS] fail() called for unknown {agent_id}")
            return

        agent.status = "failed"
        agent.error = error
        agent.finished_at = datetime.now()
        self._running_tasks.pop(agent_id, None)
        self._save()

        logger.error(f"[AGENTS] {agent_id} failed: {error}")

    def stop(self, agent_id: str) -> None:
        """
        Stop an agent manually.
        If an asyncio task is registered, it will be cancelled.
        """
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning(f"[AGENTS] stop() called for unknown {agent_id}")
            return

        # Cancel the asyncio task if it's still running
        task = self._running_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"[AGENTS] Cancelled asyncio task for {agent_id}.")

        agent.status = "stopped"
        agent.finished_at = datetime.now()
        self._save()

        logger.info(f"[AGENTS] {agent_id} stopped after {agent.elapsed_str()}.")

    # ════════════════════════════════════════════════════════
    #  Queries
    # ════════════════════════════════════════════════════════

    def list_running(self) -> list[AgentInfo]:
        """Return all agents with status 'running'."""
        return [a for a in self.agents.values() if a.status == "running"]

    def list_done(self) -> list[AgentInfo]:
        """Return all agents with a terminal status (done, failed, stopped)."""
        return [
            a for a in self.agents.values()
            if a.status in ("done", "failed", "stopped")
        ]

    def list_all(self) -> list[AgentInfo]:
        """Return all agents regardless of status."""
        return list(self.agents.values())

    # ════════════════════════════════════════════════════════
    #  Cleanup
    # ════════════════════════════════════════════════════════

    def clear_done(self) -> int:
        """
        Remove all completed / failed / stopped agents from the registry.
        Returns the number of agents removed.
        """
        to_remove = [
            aid for aid, a in self.agents.items()
            if a.status in ("done", "failed", "stopped")
        ]
        for aid in to_remove:
            del self.agents[aid]
            self._running_tasks.pop(aid, None)

        if to_remove:
            self._save()
            logger.info(
                f"[AGENTS] Cleared {len(to_remove)} finished agent(s)."
            )

        return len(to_remove)

    # ════════════════════════════════════════════════════════
    #  Async Task Registration
    # ════════════════════════════════════════════════════════

    def register_task(self, agent_id: str, task: asyncio.Task) -> None:
        """
        Associate a live asyncio.Task with an agent.

        This allows stop() to cancel the task. The mapping is NOT
        persisted — it only lives for the current process.
        """
        self._running_tasks[agent_id] = task
        logger.debug(f"[AGENTS] Registered asyncio task for {agent_id}.")
