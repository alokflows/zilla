# ============================================================
#  SETTINGS MANAGER — User Preferences & Bot Configuration
# ============================================================
#  Manages runtime-configurable settings stored in a JSON file.
#  Settings persist across bot restarts and can be changed via
#  Telegram commands without editing config.py.
# ============================================================

import json
import os
import logging
from typing import Any

from config import SETTINGS_FILE

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Default Settings
# ═══════════════════════════════════════════════════════════════

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": "gemini-3.5-flash",
    "timeout": 600,
    "progress_style": "detailed",       # detailed, minimal, silent
    "auto_describe_photos": True,
    "max_agents": 5,
    "whisper_model": "base",            # tiny, base, small
}

# Valid choices for constrained settings
VALID_PROGRESS_STYLES = {"detailed", "minimal", "silent"}
VALID_WHISPER_MODELS = {"tiny", "base", "small"}


# ═══════════════════════════════════════════════════════════════
#  SettingsManager Class
# ═══════════════════════════════════════════════════════════════

class SettingsManager:
    """
    Manages user-configurable bot settings backed by a JSON file.

    Settings are loaded once at startup and written back on every
    change, so they survive bot restarts. Unknown keys are silently
    preserved (forward-compatible).
    """

    def __init__(self, settings_file: str):
        self.settings_file = settings_file
        self.settings: dict[str, Any] = self._load()

    # ───────────────────────────────────────────────────────────
    #  Persistence
    # ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """Load settings from JSON file, merging with defaults."""
        merged = dict(DEFAULT_SETTINGS)  # start with defaults

        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                merged.update(saved)
                logger.info(f"[SETTINGS] Loaded from {self.settings_file}")
            except json.JSONDecodeError as e:
                logger.error(f"[SETTINGS] Corrupt JSON, using defaults: {e}")
            except Exception as e:
                logger.error(f"[SETTINGS] Failed to load settings: {e}")
        else:
            logger.info("[SETTINGS] No settings file found, using defaults.")

        return merged

    def _save(self) -> None:
        """Write current settings to the JSON file."""
        try:
            os.makedirs(os.path.dirname(self.settings_file) or ".", exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
            logger.debug("[SETTINGS] Saved to disk.")
        except Exception as e:
            logger.error(f"[SETTINGS] Failed to save: {e}")

    # ───────────────────────────────────────────────────────────
    #  Generic Accessors
    # ───────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value by key, with optional fallback."""
        return self.settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value and persist to disk."""
        old = self.settings.get(key)
        self.settings[key] = value
        self._save()
        logger.info(f"[SETTINGS] {key}: {old!r} → {value!r}")

    # ───────────────────────────────────────────────────────────
    #  Model
    # ───────────────────────────────────────────────────────────

    def get_model(self) -> str:
        """Return the active model identifier."""
        return self.settings.get("model", DEFAULT_SETTINGS["model"])

    def set_model(self, model_id: str) -> None:
        """Change the active model."""
        self.set("model", model_id)

    # ───────────────────────────────────────────────────────────
    #  Timeout
    # ───────────────────────────────────────────────────────────

    def get_timeout(self) -> int:
        """Return the agy request timeout in seconds."""
        return int(self.settings.get("timeout", DEFAULT_SETTINGS["timeout"]))

    def set_timeout(self, seconds: int) -> None:
        """Set the agy request timeout (clamped to 30–3600s)."""
        clamped = max(30, min(seconds, 3600))
        if clamped != seconds:
            logger.warning(
                f"[SETTINGS] Timeout {seconds}s clamped to {clamped}s (range: 30–3600)"
            )
        self.set("timeout", clamped)

    # ───────────────────────────────────────────────────────────
    #  Progress Style
    # ───────────────────────────────────────────────────────────

    def get_progress_style(self) -> str:
        """Return the progress reporting style (detailed/minimal/silent)."""
        style = self.settings.get("progress_style", DEFAULT_SETTINGS["progress_style"])
        if style not in VALID_PROGRESS_STYLES:
            logger.warning(f"[SETTINGS] Invalid progress_style '{style}', falling back to 'detailed'")
            return "detailed"
        return style

    def set_progress_style(self, style: str) -> None:
        """Set the progress reporting style."""
        if style not in VALID_PROGRESS_STYLES:
            raise ValueError(
                f"Invalid progress_style '{style}'. "
                f"Choose from: {', '.join(sorted(VALID_PROGRESS_STYLES))}"
            )
        self.set("progress_style", style)

    # ───────────────────────────────────────────────────────────
    #  Auto-Describe Photos
    # ───────────────────────────────────────────────────────────

    def get_auto_describe_photos(self) -> bool:
        """Return whether photos are automatically described by AI."""
        return bool(self.settings.get("auto_describe_photos", DEFAULT_SETTINGS["auto_describe_photos"]))

    def set_auto_describe_photos(self, enabled: bool) -> None:
        """Enable or disable automatic photo description."""
        self.set("auto_describe_photos", bool(enabled))

    # ───────────────────────────────────────────────────────────
    #  Max Agents
    # ───────────────────────────────────────────────────────────

    def get_max_agents(self) -> int:
        """Return the maximum number of concurrent agents."""
        return int(self.settings.get("max_agents", DEFAULT_SETTINGS["max_agents"]))

    def set_max_agents(self, count: int) -> None:
        """Set max concurrent agents (clamped to 1–20)."""
        clamped = max(1, min(count, 20))
        if clamped != count:
            logger.warning(
                f"[SETTINGS] max_agents {count} clamped to {clamped} (range: 1–20)"
            )
        self.set("max_agents", clamped)

    # ───────────────────────────────────────────────────────────
    #  Whisper Model
    # ───────────────────────────────────────────────────────────

    def get_whisper_model(self) -> str:
        """Return the active Whisper model size."""
        model = self.settings.get("whisper_model", DEFAULT_SETTINGS["whisper_model"])
        if model not in VALID_WHISPER_MODELS:
            logger.warning(f"[SETTINGS] Invalid whisper_model '{model}', falling back to 'base'")
            return "base"
        return model

    def set_whisper_model(self, model: str) -> None:
        """Set the Whisper model size."""
        if model not in VALID_WHISPER_MODELS:
            raise ValueError(
                f"Invalid whisper_model '{model}'. "
                f"Choose from: {', '.join(sorted(VALID_WHISPER_MODELS))}"
            )
        self.set("whisper_model", model)

    # ───────────────────────────────────────────────────────────
    #  Bulk Operations
    # ───────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, Any]:
        """Return a copy of all current settings."""
        return dict(self.settings)

    def reset(self) -> None:
        """Reset all settings back to defaults and persist."""
        self.settings = dict(DEFAULT_SETTINGS)
        self._save()
        logger.info("[SETTINGS] All settings reset to defaults.")


# ═══════════════════════════════════════════════════════════════
#  Module-Level Singleton
# ═══════════════════════════════════════════════════════════════

settings = SettingsManager(SETTINGS_FILE)
