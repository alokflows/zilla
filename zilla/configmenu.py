# ============================================================
#  CONFIG MENU — `zilla config` (Phase 2 step 2)
# ============================================================
#  Plain numbered-menu terminal settings editor. input()-based, SSH-friendly,
#  no TUI libs (Textual is the bare-`zilla` TUI's job, built separately).
#
#  Reads/writes the SAME .env + settings.json the core reads:
#    - .env            via install.read_env() / install.write_env()
#      (TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID)
#    - settings.json    via config.get_setting()/set_setting() (atomic write,
#      the SAME helper core.py and bot.py use) and the per-backend model
#      helpers added to config.py this round (get_model_for/set_model_for).
#  Never a second settings system.
#
#  Menu items 3-6 (fallback chain, voice mode, web mode, alert policy) persist
#  as plain settings keys ahead of the phases that consume them (fallback
#  chain = Phase 8, voice_mode = Phase 9, alert policy = Phase 7) — HANDOFF
#  Phase 2 step 2's job is exposing the full settings table now; wiring
#  runtime behavior to them is those later phases' job.
#
#  All I/O is dependency-injected (input_fn/print_fn) so the menu FLOW is
#  testable without a real terminal; the pure parsing helpers at the top are
#  unit-tested directly in test_zilla_cli.py.
# ============================================================

from __future__ import annotations

import install
import zilla.config as config
from zilla.backend_registry import names as backend_names

# Derived from the registry (PLAN.md §17/F2) — a new adapter (e.g. R3's
# opencode) appears here automatically, no edit needed.
BACKEND_CHOICES = backend_names()
VOICE_CHOICES = ["offline", "online"]
WEB_CHOICES = ["headless", "my-browser", "off"]
ALERT_CHOICES = ["silent", "verbose"]
# F3 (PLAN.md §17): Inbox/Outbox retention sweep. 0 = off/keep forever.
# Media/Kept is never affected by this setting regardless of value.
RETENTION_CHOICES = ["0 (off)", "30", "60", "90"]
RETENTION_VALUES = [0, 30, 60, 90]


# ── pure parsing helpers (unit-tested directly) ────────────

def mask_token(token: str) -> str:
    """Never print a secret in full. Blank -> '(not set)'."""
    token = (token or "").strip()
    if not token:
        return "(not set)"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def parse_choice(raw: str, n: int) -> int | None:
    """Parse a menu index 0..n. Returns None for anything else (including
    non-digit input) so the caller can re-prompt instead of crashing."""
    raw = (raw or "").strip()
    if not raw.lstrip("-").isdigit():
        return None
    v = int(raw)
    return v if 0 <= v <= n else None


def parse_yes_no(raw: str, default: bool) -> bool:
    raw = (raw or "").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "on")


def parse_priority_order(raw: str, valid: list[str]) -> list[str] | None:
    """Parse a comma-separated ordered list, e.g. 'agy, claude'. None if
    empty, any token isn't in `valid`, or a token repeats."""
    raw = (raw or "").strip()
    if not raw:
        return None
    items = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not items:
        return None
    seen: list[str] = []
    for it in items:
        if it not in valid or it in seen:
            return None
        seen.append(it)
    return seen


def parse_pick_from(raw: str, choices: list[str]) -> str | None:
    """Parse a 1-based numeric pick from a displayed choice list, OR the
    literal choice text (case-insensitive) — accepts either so a scripted
    round-trip (tests, `zilla config` over a pipe) can address a choice by
    name without depending on its position."""
    raw = (raw or "").strip()
    if not raw:
        return None
    idx = parse_choice(raw, len(choices))
    if idx is not None and idx >= 1:
        return choices[idx - 1]
    low = raw.lower()
    return low if low in choices else None


# ── the menu itself ─────────────────────────────────────────

def _hr(print_fn):
    print_fn("=" * 56)


def _current_state() -> dict:
    backend = config.get_backend()
    return {
        "backend": backend,
        "priority": config.get_setting("backend_priority", [backend]),
        "fallback": bool(config.get_setting("fallback_enabled", False)),
        "voice": config.get_setting("voice_mode", "online"),
        "web": config.get_setting("web_mode", "off"),
        "alert": config.get_setting("alert_policy", "silent"),
        "autostart": bool(config.get_setting("autostart_enabled", False)),
        "retention": config.get_media_retention_days(),
    }


def _print_menu(print_fn, state: dict, env: dict):
    _hr(print_fn)
    print_fn("  Zilla — settings")
    _hr(print_fn)
    print_fn(f"  1) Backend priority & active backend   (active: {state['backend']}, "
              f"order: {','.join(state['priority'])})")
    models = " | ".join(f"{b}: {config.get_model_for(b)}" for b in BACKEND_CHOICES)
    print_fn(f"  2) Model per backend                   ({models})")
    print_fn(f"  3) Fallback chain                      ({'on' if state['fallback'] else 'off'})")
    print_fn(f"  4) Voice mode                          ({state['voice']})")
    print_fn(f"  5) Web mode                            ({state['web']})")
    print_fn(f"  6) Health & alert policy               ({state['alert']})")
    print_fn(f"  7) Telegram connector                  (token: "
              f"{mask_token(env.get('TELEGRAM_BOT_TOKEN', ''))} | "
              f"owner: {env.get('TELEGRAM_OWNER_ID') or '(not set)'})")
    print_fn(f"  8) Autostart                           ({'on' if state['autostart'] else 'off'})")
    print_fn(f"  9) Media storage retention              "
              f"({state['retention']} days, 0=off; Kept/ is always exempt)")
    print_fn("  0) Exit")
    _hr(print_fn)


def _menu_backend(input_fn, print_fn):
    print_fn(f"  Available: {', '.join(BACKEND_CHOICES)}")
    raw = input_fn("  Priority order, comma-separated (e.g. agy,claude): ").strip()
    order = parse_priority_order(raw, BACKEND_CHOICES)
    if order is None:
        print_fn("  Not saved — enter a comma-separated list using only: "
                  + ", ".join(BACKEND_CHOICES))
        return
    config.set_setting("backend_priority", order)
    config.set_backend(order[0])
    print_fn(f"  Saved. Active backend = {config.get_backend()}, "
              f"priority = {config.get_setting('backend_priority')}")


def _menu_model(input_fn, print_fn):
    raw = input_fn(f"  Which backend? ({'/'.join(BACKEND_CHOICES)}): ").strip().lower()
    if raw not in BACKEND_CHOICES:
        print_fn(f"  Not saved — choose one of: {', '.join(BACKEND_CHOICES)}.")
        return
    catalog = config.model_catalog_for(raw)
    if not catalog:
        print_fn(f"  No models available for '{raw}' right now.")
        return
    for i, (label, _val) in enumerate(catalog, 1):
        print_fn(f"    {i}) {label}")
    pick = parse_choice(input_fn(f"  Pick 1-{len(catalog)}: "), len(catalog))
    if not pick:
        print_fn("  Not saved.")
        return
    _label, value = catalog[pick - 1]
    stored = config.set_model_for(raw, value)
    print_fn(f"  Saved. {raw} model = {stored}")


def _menu_fallback(input_fn, print_fn, state):
    cur = state["fallback"]
    raw = input_fn(f"  Enable fallback chain? (y/n) [{'y' if cur else 'n'}]: ")
    val = parse_yes_no(raw, cur)
    config.set_setting("fallback_enabled", val)
    print_fn(f"  Saved. fallback_enabled = {config.get_setting('fallback_enabled')}")


def _menu_pick_setting(input_fn, print_fn, label: str, key: str, choices: list[str], current: str):
    print_fn(f"  {label}: {', '.join(choices)}")
    raw = input_fn(f"  Choose [{current}]: ")
    picked = parse_pick_from(raw, choices) if raw.strip() else current
    if picked is None:
        print_fn(f"  Not saved — choose one of: {', '.join(choices)}")
        return
    config.set_setting(key, picked)
    print_fn(f"  Saved. {key} = {config.get_setting(key)}")


def _menu_telegram(input_fn, print_fn):
    env = install.read_env()
    cur_token = env.get("TELEGRAM_BOT_TOKEN", "")
    cur_owner = env.get("TELEGRAM_OWNER_ID", "")
    print_fn(f"  Current token: {mask_token(cur_token)}")
    new_token = input_fn("  New bot token (Enter to keep current): ").strip()
    token = new_token or cur_token
    if new_token:
        ok, detail = install.validate_token(token)
        print_fn(f"  {'Token OK — ' + detail if ok else 'Token check failed — ' + detail}")
    owner_raw = input_fn(f"  Owner Telegram ID (Enter to keep '{cur_owner or 'unset'}'): ").strip()
    owner = owner_raw or cur_owner
    if owner and not owner.isdigit():
        print_fn("  Not saved — owner ID must be numeric.")
        return
    install.write_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_ID": owner})
    saved = install.read_env()
    print_fn(f"  Saved. token = {mask_token(saved.get('TELEGRAM_BOT_TOKEN', ''))}, "
              f"owner = {saved.get('TELEGRAM_OWNER_ID') or '(not set)'}")


def _menu_autostart(input_fn, print_fn, state):
    cur = state["autostart"]
    raw = input_fn(f"  Autostart at login? (y/n) [{'y' if cur else 'n'}]: ")
    want = parse_yes_no(raw, cur)
    if want == cur:
        print_fn(f"  Unchanged. autostart = {'on' if cur else 'off'}")
        return
    if want:
        install.setup_autostart()
    else:
        install.disable_autostart()
    config.set_setting("autostart_enabled", want)
    print_fn(f"  Saved. autostart = {'on' if want else 'off'}")


def _menu_retention(input_fn, print_fn, state):
    cur = state["retention"]
    cur_label = RETENTION_CHOICES[RETENTION_VALUES.index(cur)] if cur in RETENTION_VALUES else str(cur)
    print_fn(f"  Media retention (days): {', '.join(RETENTION_CHOICES)}")
    raw = input_fn(f"  Choose [{cur_label}]: ")
    picked = parse_pick_from(raw, RETENTION_CHOICES) if raw.strip() else cur_label
    if picked is None:
        print_fn(f"  Not saved — choose one of: {', '.join(RETENTION_CHOICES)}")
        return
    days = RETENTION_VALUES[RETENTION_CHOICES.index(picked)]
    config.set_setting("media_retention_days", days)
    print_fn(f"  Saved. media_retention_days = {config.get_media_retention_days()}")


def run_menu(input_fn=input, print_fn=print) -> None:
    """Main interactive loop. Exits on choice 0 (or q/quit/exit)."""
    while True:
        state = _current_state()
        env = install.read_env()
        _print_menu(print_fn, state, env)
        raw = input_fn("Choose [0-9]: ")
        if raw.strip().lower() in ("q", "quit", "exit"):
            print_fn("  Bye.")
            return
        choice = parse_choice(raw, 9)
        if choice is None:
            print_fn("  Not a valid choice.\n")
            continue
        if choice == 0:
            print_fn("  Bye.")
            return
        print_fn("")
        if choice == 1:
            _menu_backend(input_fn, print_fn)
        elif choice == 2:
            _menu_model(input_fn, print_fn)
        elif choice == 3:
            _menu_fallback(input_fn, print_fn, state)
        elif choice == 4:
            _menu_pick_setting(input_fn, print_fn, "Voice mode", "voice_mode",
                                VOICE_CHOICES, state["voice"])
        elif choice == 5:
            _menu_pick_setting(input_fn, print_fn, "Web mode", "web_mode",
                                WEB_CHOICES, state["web"])
        elif choice == 6:
            _menu_pick_setting(input_fn, print_fn, "Health & alert policy", "alert_policy",
                                ALERT_CHOICES, state["alert"])
        elif choice == 7:
            _menu_telegram(input_fn, print_fn)
        elif choice == 8:
            _menu_autostart(input_fn, print_fn, state)
        elif choice == 9:
            _menu_retention(input_fn, print_fn, state)
        print_fn("")
