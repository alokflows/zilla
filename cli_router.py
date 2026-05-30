# ============================================================
#  CLI ROUTER — Multi-Backend AI CLI Abstraction
# ============================================================
#  Routes messages to different AI CLI backends:
#  - Antigravity (agy.exe) — Default, current implementation
#  - Claude Code CLI — Anthropic's claude command
#  - Gemini CLI — Google's gemini command  
#  - Ollama — Local models
#
#  Each backend implements the CLIBackend interface.
#  The router selects the active backend and delegates.
# ============================================================

import os
import shutil
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Callable

logger = logging.getLogger(__name__)


class CLIBackend(ABC):
    """Abstract base class for AI CLI backends."""

    @abstractmethod
    def send(self, text: str, conversation_id: Optional[str] = None,
             timeout: int = 600, progress_callback: Optional[Callable] = None
             ) -> Tuple[str, Optional[str]]:
        """
        Send a message to the AI backend.
        
        Args:
            text: User message
            conversation_id: Existing conversation ID (or None for new)
            timeout: Max seconds to wait
            progress_callback: Optional progress update callback
            
        Returns:
            (response_text, conversation_id)
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is installed and accessible."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Human-readable backend name."""
        pass

    @abstractmethod
    def get_id(self) -> str:
        """Backend identifier for config."""
        pass


class AntigravityBackend(CLIBackend):
    """Antigravity CLI (agy.exe) — Current default backend."""

    def __init__(self):
        from config import AGY_PATH
        self.agy_path = AGY_PATH

    def send(self, text, conversation_id=None, timeout=600, progress_callback=None):
        from agy_runner import run_agy_pty
        return run_agy_pty(text, conversation_id, timeout=timeout,
                          progress_callback=progress_callback)

    def is_available(self) -> bool:
        return os.path.isfile(self.agy_path)

    def get_name(self) -> str:
        return "Antigravity CLI"

    def get_id(self) -> str:
        return "agy"


class ClaudeCodeBackend(CLIBackend):
    """Claude Code CLI — Anthropic's coding assistant."""

    def __init__(self):
        self._binary = shutil.which("claude")

    def send(self, text, conversation_id=None, timeout=600, progress_callback=None):
        # Placeholder — will be implemented when Claude Code integration is needed
        raise NotImplementedError(
            "Claude Code backend is detected but not yet fully integrated. "
            "Use Antigravity CLI as the default backend."
        )

    def is_available(self) -> bool:
        return self._binary is not None

    def get_name(self) -> str:
        return "Claude Code"

    def get_id(self) -> str:
        return "claude"


class OllamaBackend(CLIBackend):
    """Ollama — Local model runner."""

    def __init__(self):
        self._binary = shutil.which("ollama")

    def send(self, text, conversation_id=None, timeout=600, progress_callback=None):
        raise NotImplementedError(
            "Ollama backend is detected but not yet fully integrated. "
            "Use Antigravity CLI as the default backend."
        )

    def is_available(self) -> bool:
        return self._binary is not None

    def get_name(self) -> str:
        return "Ollama (Local)"

    def get_id(self) -> str:
        return "ollama"


# ════════════════════════════════════════════════════════════
#  CLI ROUTER — Backend Selection & Routing
# ════════════════════════════════════════════════════════════

class CLIRouter:
    """Routes messages to the active CLI backend."""

    def __init__(self):
        self._backends: dict[str, CLIBackend] = {}
        self._active_id: str = "agy"
        self._discover_backends()

    def _discover_backends(self):
        """Auto-discover available CLI backends."""
        backend_classes = [
            AntigravityBackend,
            ClaudeCodeBackend,
            OllamaBackend,
        ]
        for cls in backend_classes:
            try:
                backend = cls()
                self._backends[backend.get_id()] = backend
                if backend.is_available():
                    logger.info("[CLI] Found backend: %s", backend.get_name())
                else:
                    logger.debug("[CLI] Backend not available: %s", backend.get_name())
            except Exception as e:
                logger.debug("[CLI] Failed to init backend %s: %s", cls.__name__, e)

    @property
    def active_backend(self) -> CLIBackend:
        return self._backends.get(self._active_id, list(self._backends.values())[0])

    @property
    def active_id(self) -> str:
        return self._active_id

    @active_id.setter 
    def active_id(self, backend_id: str):
        if backend_id in self._backends:
            self._active_id = backend_id
            logger.info("[CLI] Switched to backend: %s", self._backends[backend_id].get_name())
        else:
            logger.warning("[CLI] Unknown backend: %s", backend_id)

    def send(self, text: str, conversation_id: Optional[str] = None,
             timeout: int = 600, progress_callback: Optional[Callable] = None
             ) -> Tuple[str, Optional[str]]:
        """Route message to active backend."""
        backend = self.active_backend
        logger.info("[CLI] Routing to %s", backend.get_name())
        return backend.send(text, conversation_id, timeout, progress_callback)

    def list_available(self) -> list[dict]:
        """List all backends with availability status."""
        result = []
        for bid, backend in self._backends.items():
            result.append({
                'id': bid,
                'name': backend.get_name(),
                'available': backend.is_available(),
                'active': bid == self._active_id,
            })
        return result

    def list_available_names(self) -> list[str]:
        """List names of available backends for dropdown."""
        return [b.get_name() for b in self._backends.values()]

    def get_id_by_name(self, name: str) -> Optional[str]:
        """Get backend ID from display name."""
        for bid, backend in self._backends.items():
            if backend.get_name() == name:
                return bid
        return None

    def get_name_by_id(self, bid: str) -> str:
        """Get display name from backend ID."""
        if bid in self._backends:
            return self._backends[bid].get_name()
        return "Unknown"


# ── Global singleton ──────────────────────────────────────
cli_router = CLIRouter()
