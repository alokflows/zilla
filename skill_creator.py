# ============================================================
#  SKILL AUTO-CREATOR — Zilla Self-Learning Engine
# ============================================================
#  This module provides the system prompt and logic that enables
#  Zilla to automatically create and save new skills when it
#  learns useful patterns, scripts, or workflows.
#
#  When the user asks Zilla to do something repeatedly or
#  creates a useful tool, Zilla can save it as a skill for
#  future reuse.
# ============================================================

import os
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# The system prompt injected into the AI context to enable
# automatic skill creation.
SKILL_CREATION_PROMPT = """
## 🧩 Skill Auto-Creation

You have the ability to create reusable SKILLS — modular instruction files that
extend your capabilities. When you solve a complex or repetitive task, you SHOULD
proactively create a skill for future use.

### When to Create a Skill
- When the user asks you to do something you might need to repeat
- When you build a useful script, automation, or workflow
- When you discover a pattern that could benefit from structured instructions
- When the user explicitly asks to "save this as a skill"

### How to Create a Skill
To create a skill, write a SKILL.md file in the skills directory with this format:

```markdown
---
name: <skill-name>
description: <one-line description of what the skill does>
---

# <Skill Name>

## Overview
[Brief description of what this skill does and when to use it]

## Instructions
[Detailed step-by-step instructions for executing this skill]

## Inputs
[What information this skill needs from the user or environment]

## Output Format
[How results should be formatted and presented]

## Examples
[Usage examples with expected inputs and outputs]

## Scripts
[Reference any helper scripts in the scripts/ subdirectory]
```

### Skill Directory Structure
Each skill should be a folder containing:
- `SKILL.md` — The main instruction file (required)
- `scripts/` — Helper scripts and utilities (optional)
- `references/` — Additional documentation (optional)
- `examples/` — Reference implementations (optional)

### Skills Location
Save skills to: {skills_dir}

### Important Rules
1. Use kebab-case for folder names (e.g., `web-scraper`, `data-analyzer`)
2. Always include YAML frontmatter with name and description
3. Make skills self-contained and reusable
4. Include clear examples so the skill can be executed by another AI instance
5. When creating scripts, make them cross-platform when possible
"""


def get_skill_creation_prompt(skills_dir: str) -> str:
    """Get the skill creation system prompt with the actual skills directory."""
    return SKILL_CREATION_PROMPT.format(skills_dir=skills_dir)


def get_installed_skills_context(skills_dir: str) -> str:
    """
    Build a context string listing all installed skills for the AI.
    This helps the AI know what skills are available before creating duplicates.
    """
    if not os.path.isdir(skills_dir):
        return "No skills installed."

    lines = ["## Currently Installed Skills\n"]
    try:
        for entry in sorted(os.listdir(skills_dir)):
            skill_dir = os.path.join(skills_dir, entry)
            if not os.path.isdir(skill_dir):
                continue

            skill_md = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue

            name = entry
            description = ""

            # Parse frontmatter for display name
            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read(500)  # Only read header
                    name_match = re.search(r'^name:\s*(.+)$', content, re.MULTILINE)
                    desc_match = re.search(r'^description:\s*(.+)$', content, re.MULTILINE)
                    if name_match:
                        name = name_match.group(1).strip()
                    if desc_match:
                        description = desc_match.group(1).strip()
            except Exception:
                pass

            lines.append(f"- **{name}** (`{entry}/`) — {description}")

    except OSError:
        return "Could not read skills directory."

    if len(lines) == 1:
        return "No skills installed."

    return "\n".join(lines)


def auto_create_skill(skills_dir: str, name: str, description: str,
                      instructions: str, scripts: dict = None) -> str:
    """
    Programmatically create a new skill.

    Args:
        skills_dir: Path to the skills root directory
        name: Human-readable skill name
        description: One-line description
        instructions: Full markdown instruction content
        scripts: Optional dict of {filename: content} for helper scripts

    Returns:
        Path to the created skill directory, or error message
    """
    folder_name = name.lower().replace(" ", "-").replace("_", "-")
    folder_name = re.sub(r'[^a-z0-9-]', '', folder_name)

    skill_dir = os.path.join(skills_dir, folder_name)

    if os.path.exists(skill_dir):
        return f"ERROR: Skill '{folder_name}' already exists at {skill_dir}"

    try:
        os.makedirs(skill_dir, exist_ok=True)

        # Write SKILL.md
        skill_md_content = f"""---
name: {name}
description: {description}
---

{instructions}
"""
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_md_content)

        # Write scripts if provided
        if scripts:
            scripts_dir = os.path.join(skill_dir, "scripts")
            os.makedirs(scripts_dir, exist_ok=True)
            for filename, content in scripts.items():
                with open(os.path.join(scripts_dir, filename), "w", encoding="utf-8") as f:
                    f.write(content)

        # Create standard subdirectories
        for subdir in ["references", "examples"]:
            os.makedirs(os.path.join(skill_dir, subdir), exist_ok=True)

        logger.info("[SKILLS] Auto-created skill: %s at %s", name, skill_dir)
        return skill_dir

    except Exception as e:
        logger.error("[SKILLS] Failed to auto-create skill %s: %s", name, e)
        return f"ERROR: {e}"
