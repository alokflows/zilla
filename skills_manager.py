# ============================================================
#  SKILLS MANAGER — Antigravity CLI Skills
# ============================================================
#  Manages skills installed in the antigravity-cli skills dir.
#
#  Each skill is a subdirectory containing a SKILL.md file
#  with YAML frontmatter (name, description).
#
#  Skills directory:
#    C:\Users\Isha\.gemini\antigravity-cli\skills\
#      ├── kimi-webbridge/
#      │   └── SKILL.md
#      ├── another-skill/
#      │   └── SKILL.md
#      └── ...
# ============================================================

import os
import re
import shutil
import logging
from typing import Optional

from config import SKILLS_DIR

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  SkillInfo — Data class for a single skill
# ════════════════════════════════════════════════════════════

class SkillInfo:
    """Represents an installed skill with its metadata."""

    def __init__(self, folder_name: str, name: str, description: str, path: str):
        self.folder_name = folder_name    # Directory name (e.g. "kimi-webbridge")
        self.name = name                  # Display name from YAML frontmatter
        self.description = description    # Description from YAML frontmatter
        self.path = path                  # Full path to skill directory
        self.skill_md_path = os.path.join(path, "SKILL.md")

    def to_dict(self) -> dict:
        """Serialize skill info to a dictionary."""
        return {
            "folder_name": self.folder_name,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "skill_md_path": self.skill_md_path,
        }

    def __repr__(self) -> str:
        return f"SkillInfo(folder_name={self.folder_name!r}, name={self.name!r})"


# ════════════════════════════════════════════════════════════
#  SkillsManager — Discover, read, and manage skills
# ════════════════════════════════════════════════════════════

class SkillsManager:
    """Manages Antigravity CLI skills installed on disk."""

    def __init__(self, skills_dir: str = SKILLS_DIR):
        self.skills_dir = skills_dir

    # ── List all installed skills ──────────────────────────

    def list_skills(self) -> list[SkillInfo]:
        """
        Scan the skills directory and return all installed skills.

        Each subdirectory containing a SKILL.md file is treated as
        an installed skill.  YAML frontmatter is parsed for metadata.
        """
        skills: list[SkillInfo] = []

        if not os.path.isdir(self.skills_dir):
            logger.warning("[SKILLS] Skills directory does not exist: %s", self.skills_dir)
            return skills

        try:
            entries = sorted(os.listdir(self.skills_dir))
        except OSError as exc:
            logger.error("[SKILLS] Failed to list skills directory: %s", exc)
            return skills

        for entry in entries:
            entry_path = os.path.join(self.skills_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            skill_md_path = os.path.join(entry_path, "SKILL.md")
            if not os.path.isfile(skill_md_path):
                logger.debug("[SKILLS] Skipping %s — no SKILL.md found", entry)
                continue

            name, description = self._parse_skill_md(skill_md_path)
            skill = SkillInfo(
                folder_name=entry,
                name=name,
                description=description,
                path=entry_path,
            )
            skills.append(skill)
            logger.debug("[SKILLS] Found skill: %s (%s)", name, entry)

        logger.info("[SKILLS] Discovered %d skill(s)", len(skills))
        return skills

    # ── Get a single skill by folder name ──────────────────

    def get_skill(self, folder_name: str) -> Optional[SkillInfo]:
        """
        Look up a single skill by its directory name.

        Returns None if the skill doesn't exist or has no SKILL.md.
        """
        skill_path = os.path.join(self.skills_dir, folder_name)

        if not os.path.isdir(skill_path):
            logger.debug("[SKILLS] Skill directory not found: %s", folder_name)
            return None

        skill_md_path = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md_path):
            logger.debug("[SKILLS] No SKILL.md in: %s", folder_name)
            return None

        name, description = self._parse_skill_md(skill_md_path)
        return SkillInfo(
            folder_name=folder_name,
            name=name,
            description=description,
            path=skill_path,
        )

    # ── Read full SKILL.md content ─────────────────────────

    def get_skill_content(self, folder_name: str) -> Optional[str]:
        """
        Read the full SKILL.md content for a skill.

        Returns None if the skill doesn't exist.
        """
        skill_path = os.path.join(self.skills_dir, folder_name)
        skill_md_path = os.path.join(skill_path, "SKILL.md")

        if not os.path.isfile(skill_md_path):
            logger.warning("[SKILLS] SKILL.md not found for: %s", folder_name)
            return None

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.debug("[SKILLS] Read SKILL.md for %s (%d bytes)", folder_name, len(content))
            return content
        except OSError as exc:
            logger.error("[SKILLS] Failed to read SKILL.md for %s: %s", folder_name, exc)
            return None

    # ── Remove an installed skill ──────────────────────────

    def remove_skill(self, folder_name: str) -> bool:
        """
        Remove a skill by deleting its directory.

        Returns True if the skill was removed, False otherwise.
        """
        skill_path = os.path.join(self.skills_dir, folder_name)

        if not os.path.isdir(skill_path):
            logger.warning("[SKILLS] Cannot remove — directory not found: %s", folder_name)
            return False

        try:
            shutil.rmtree(skill_path)
            logger.info("[SKILLS] Removed skill: %s", folder_name)
            return True
        except OSError as exc:
            logger.error("[SKILLS] Failed to remove skill %s: %s", folder_name, exc)
            return False

    # ── Generate skills summary text ───────────────────────

    def get_skills_summary(self) -> str:
        """
        Generate a human-readable summary of all installed skills.

        Used to inject skill descriptions into bot_instructions.md
        so the agent knows what capabilities are available.

        Returns a bulleted list like:
          - Kimi WebBridge: Browser automation — navigate, click, type, screenshot
          - Another Skill: Does something useful
        """
        skills = self.list_skills()

        if not skills:
            return "No skills installed."

        lines: list[str] = []
        for skill in skills:
            if skill.description:
                lines.append(f"- {skill.name}: {skill.description}")
            else:
                lines.append(f"- {skill.name}")

        return "\n".join(lines)

    # ── Parse YAML frontmatter from SKILL.md ───────────────

    def _parse_skill_md(self, skill_md_path: str) -> tuple[str, str]:
        """
        Parse YAML frontmatter from a SKILL.md file.

        Extracts 'name' and 'description' fields from the YAML
        block delimited by --- markers at the top of the file.

        Uses simple regex/string parsing — no pyyaml dependency.

        Example SKILL.md:
            ---
            name: Kimi WebBridge
            description: Browser automation skill
            ---
            # Full skill instructions below...

        Returns:
            (name, description) — falls back to the parent folder
            name if the frontmatter is missing or malformed.
        """
        # Defaults: derive from parent folder name
        folder_name = os.path.basename(os.path.dirname(skill_md_path))
        default_name = folder_name.replace("-", " ").replace("_", " ").title()
        name = default_name
        description = ""

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            logger.error("[SKILLS] Failed to read %s: %s", skill_md_path, exc)
            return name, description

        # Match YAML frontmatter: --- ... ---
        frontmatter_match = re.match(
            r"^---\s*\n(.*?)\n---",
            content,
            re.DOTALL,
        )
        if not frontmatter_match:
            logger.debug("[SKILLS] No YAML frontmatter in %s", skill_md_path)
            return name, description

        frontmatter = frontmatter_match.group(1)

        # Extract 'name' field
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        if name_match:
            parsed_name = name_match.group(1).strip().strip("\"'")
            if parsed_name:
                name = parsed_name

        # Extract 'description' field
        desc_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
        if desc_match:
            parsed_desc = desc_match.group(1).strip().strip("\"'")
            if parsed_desc:
                description = parsed_desc

        return name, description
