# ============================================================
#  KEYBOARDS — all inline-menu / button builders
# ============================================================
#  Pure UI layer: every function here turns plain data (counts,
#  item lists, the current model, the user id) into a Telegram
#  InlineKeyboardMarkup. No business logic, no network I/O.
#
#  Every label here obeys `docs/dev/STYLE.md` (Phase U3): sentence
#  case, no decorative emoji (the accent emoji lives in the screen
#  title, which bot.py owns), only the functional glyphs of R13,
#  primary action above the nav row, `✕ Close` always last-row-right.
#
#  The only runtime dependency on the rest of the bot is `auth`
#  (the AuthManager instance), which bot.py injects at startup
#  via `keyboards.auth = auth`. It is None until then, but these
#  builders only run when a user opens a menu — long after boot.
# ============================================================

from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    get_setting, get_idle_kill_after, get_backend, model_catalog,
    get_media_retention_days,
)
from media import format_file_size, keep_token
from zilla.backend_registry import installed_backends

# Injected by bot.py at startup (see module docstring).
auth = None

# Idle-reaper cycle options shown in Settings.
_IDLE_OPTIONS = [
    (120, "2 min — quick"),
    (180, "3 min — normal"),
    (300, "5 min — patient"),
    (0, "Never"),
]

# F3: media retention sweep options (owner-facing, button values only —
# never free text, per PLAN.md §17). 0 = sweep disabled.
_RETENTION_OPTIONS = [
    (0, "Off — keep forever"),
    (30, "30 days"),
    (60, "60 days"),
    (90, "90 days"),
]

# Inbox/Outbox pagination + category metadata.
INBOX_PAGE = 10
# Labels carry no emoji (STYLE.md R12) — the accent icon is in the panel title.
INBOX_CAT_META = [
    ("images", "Images"),
    ("audio", "Audio"),
    ("video", "Video"),
    ("documents", "Documents"),
]
# Outbox (agent-produced files) — same UX as the Inbox, but no audio.
OUTBOX_CAT_META = [
    ("images", "Images"),
    ("video", "Video"),
    ("documents", "Documents"),
]


def _close_btn():
    return InlineKeyboardButton("✕ Close", callback_data="menu_close")


def _can_change_model(uid: int) -> bool:
    """Owner always; admins only if the owner has enabled it."""
    if not auth:
        return False
    return auth.can_change_model(uid, get_setting("admins_can_change_model", True))


def _idle_label(val: int) -> str:
    for v, label in _IDLE_OPTIONS:
        if v == val:
            return label
    return f"{val} seconds"


def _retention_label(days: int) -> str:
    for v, label in _RETENTION_OPTIONS:
        if v == days:
            return label
    return f"{days} days"


def _fmt_next(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%a %d %b %H:%M")


def kb_menu(uid: int = 0):
    is_admin = bool(auth and auth.can(uid, "admin"))
    rows = [
        [InlineKeyboardButton("Sessions", callback_data="menu_sessions"),
         InlineKeyboardButton("Inbox", callback_data="menu_inbox")],
        [InlineKeyboardButton("Outbox", callback_data="menu_outbox")],
    ]
    if is_admin:
        # Schedules previously had NO menu entry (command-only) — added here.
        rows.append([
            InlineKeyboardButton("Schedules", callback_data="menu_schedules"),
            InlineKeyboardButton("Settings", callback_data="menu_settings"),
        ])
        model_row = [InlineKeyboardButton("Browse", callback_data="menu_browse")]
        if _can_change_model(uid):
            model_row.insert(0, InlineKeyboardButton("Model", callback_data="menu_model"))
        rows.append(model_row)
    rows.append([
        InlineKeyboardButton("Status", callback_data="menu_status"),
        InlineKeyboardButton("Health", callback_data="menu_health"),
    ])
    if auth and auth.is_owner(uid):
        rows.append([InlineKeyboardButton("Users", callback_data="menu_users")])
    rows.append([_close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_sessions(all_sessions: dict, active: str):
    buttons = []
    for name, info in all_sessions.items():
        # State glyph leads the label (STYLE.md R20); ✓ marks the live session.
        marker = "✓ " if name == active else ""
        msgs = info.get("messages", 0)
        # Switch on the left, delete (🗑) on the right of the same row.
        buttons.append([
            InlineKeyboardButton(
                f"{marker}{name} · {msgs} messages",
                callback_data=f"sess_switch_{name}",
            ),
            InlineKeyboardButton("🗑", callback_data=f"sess_delete_{name}"),
        ])
    # Action above navigation (R16); Close last-row-right (R15).
    buttons.append([InlineKeyboardButton("➕ New session", callback_data="sess_new")])
    buttons.append([
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
        _close_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_session_delete(name: str):
    return InlineKeyboardMarkup([
        # R19: the confirm button names the action; cancel sits on the right.
        [InlineKeyboardButton("🗑 Delete session", callback_data=f"sess_confirm_del_{name}"),
         InlineKeyboardButton("Cancel", callback_data="sess_list")],
    ])


def kb_model(current: str):
    """Model picker for the ACTIVE backend (agy=Gemini×effort, claude=Opus/Sonnet/Haiku).
    ✓ marks the live value. Catalog comes from config.model_catalog()."""
    catalog = model_catalog()        # list of (label, value)
    buttons, row = [], []
    per_row = 3 if len(catalog) > 4 else 1
    for label, value in catalog:
        mark = "✓ " if value == current else ""
        row.append(InlineKeyboardButton(f"{mark}{label}", callback_data=f"model_{value}"))
        if len(row) == per_row:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Custom…", callback_data="model_custom")])
    # PLAN.md §17/F2: every OTHER backend that's actually installed gets a
    # button — no button for one that isn't, and a 3rd registered backend
    # (e.g. R3's opencode) shows up here with zero edits to this file.
    active = get_backend()
    switch_row = [
        InlineKeyboardButton(f"Use {a.name}", callback_data=f"model_use_{a.name}")
        for a in installed_backends() if a.name != active
    ]
    if switch_row:
        buttons.append(switch_row)
    buttons.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(buttons)


def kb_settings(uid: int = 0):
    auto_photo = get_setting("auto_describe_photos", False)
    rows = [
        [InlineKeyboardButton(
            f"Describe photos: {'on' if auto_photo else 'off'}",
            callback_data="set_toggle_photo",
        )],
    ]
    if auth and auth.can(uid, "admin"):
        idle_kill = get_idle_kill_after()
        rows.append([InlineKeyboardButton(
            f"Close idle chats: {_idle_label(idle_kill)}",
            callback_data="set_cycle_idle",
        )])
    if auth and auth.can(uid, "admin"):
        catchup = get_setting("schedule_catchup", True)
        rows.append([InlineKeyboardButton(
            f"Catch up missed schedules: {'on' if catchup else 'off'}",
            callback_data="set_toggle_catchup",
        )])
    if auth and auth.is_owner(uid):
        admins_model = get_setting("admins_can_change_model", True)
        rows.append([InlineKeyboardButton(
            f"Admins can change model: {'on' if admins_model else 'off'}",
            callback_data="set_toggle_admin_model",
        )])
        rows.append([InlineKeyboardButton(
            f"AI engine: {get_backend()}", callback_data="noop",
        )])
        # PLAN.md §17/F2: one button per OTHER installed backend, derived
        # from the registry — not a fixed 2-way toggle.
        active = get_backend()
        switch_row = [
            InlineKeyboardButton(f"Use {a.name}", callback_data=f"set_backend_{a.name}")
            for a in installed_backends() if a.name != active
        ]
        if switch_row:
            rows.append(switch_row)
        rows.append([InlineKeyboardButton(
            f"Storage: {_retention_label(get_media_retention_days())}",
            callback_data="set_storage",
        )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_settings_storage():
    """F3: owner-only Inbox/Outbox retention picker — button values only.
    Media/Kept is never affected by this setting (permanent, sweep-exempt)."""
    current = get_media_retention_days()
    rows = [
        [InlineKeyboardButton(
            ("✅ " if v == current else "") + label,
            callback_data=f"set_retention_{v}",
        )]
        for v, label in _RETENTION_OPTIONS
    ]
    rows.append([InlineKeyboardButton("◀ Settings", callback_data="menu_settings"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()],
    ])


def kb_health():
    """Phase F4 (PLAN.md §17): /health gains one entry point into the
    System jobs sub-panel (heartbeat/distillation/etc — deliberately
    absent from /schedules now)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("System jobs", callback_data="menu_sysjobs")],
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()],
    ])


def kb_sysjobs(items: list):
    """Row per system job: [state · title] toggles pause/resume.
    Never a delete button — system jobs are pausable only (ScheduleManager.
    remove refuses them)."""
    rows = []
    for s in items:
        # One line, no timestamp: the panel text above already carries
        # "last run" (R4/R18 — the button must not wrap).
        state = "✅" if s.get("enabled") else "⏸"
        title = s.get("title", "")[:28]
        rows.append([InlineKeyboardButton(
            f"{state} {title}",
            callback_data=f"sysjob_toggle_{s['id']}",
        )])

    rows.append([InlineKeyboardButton("◀ Health", callback_data="menu_health"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_error():
    return InlineKeyboardMarkup([
        # R5/R14: the recovery action first, plainly named. No Close on a
        # card attached to a message (R20).
        [InlineKeyboardButton("Try again", callback_data="err_retry"),
         InlineKeyboardButton("Change model", callback_data="err_model")],
    ])


def kb_users(users: dict):
    buttons = []
    for uid_int, info in users.items():
        name = info.get("name") or f"User {uid_int}"
        role = info.get("role", "admin")
        buttons.append([InlineKeyboardButton(
            f"{name} · {role}", callback_data=f"user_detail_{uid_int}",
        )])
    buttons.append([InlineKeyboardButton("➕ Add user", callback_data="user_add_start")])
    buttons.append([
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
        _close_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_user_detail(target_id: int, role: str = "admin"):
    # Role toggle: admins have full unattended access; "limited" users can chat
    # but every request waits for the owner's approval (Approval mode).
    if role == "limited":
        toggle = InlineKeyboardButton(
            "Give full access", callback_data=f"user_role_admin_{target_id}")
    else:
        toggle = InlineKeyboardButton(
            "Put in approval mode", callback_data=f"user_role_limited_{target_id}")
    return InlineKeyboardMarkup([
        [toggle],
        [InlineKeyboardButton("🗑 Remove", callback_data=f"user_remove_{target_id}"),
         InlineKeyboardButton("◀ Back", callback_data="user_list")],
    ])


def kb_inbox_categories(counts: dict):
    rows = []
    for cat, label in INBOX_CAT_META:
        n = counts.get(cat, 0)
        if n:
            rows.append([InlineKeyboardButton(
                f"{label} ({n})", callback_data=f"ibx_cat_{cat}_0",
            )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_inbox_list(category: str, items: list, offset: int):
    """One row per file in this page: [ name (size) | 📤 ]; both send it."""
    rows = []
    page = items[offset:offset + INBOX_PAGE]
    for i, item in enumerate(page):
        idx = offset + i
        name = item["name"]
        # 18 chars: this row holds three buttons (STYLE.md R4/R17).
        label = name if len(name) <= 18 else name[:17] + "…"
        send_cb = f"ibx_send_{category}_{idx}"
        rows.append([
            InlineKeyboardButton(
                f"{label} ({format_file_size(item['size'])})", callback_data=send_cb),
            InlineKeyboardButton("📤", callback_data=send_cb),
            InlineKeyboardButton("🗑", callback_data=f"ibx_del_{category}_{idx}"),
        ])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            "◀ Previous", callback_data=f"ibx_cat_{category}_{max(0, offset - INBOX_PAGE)}"))
    if offset + INBOX_PAGE < len(items):
        nav.append(InlineKeyboardButton(
            "Next ▶", callback_data=f"ibx_cat_{category}_{offset + INBOX_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Categories", callback_data="menu_inbox"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_keep(path: str):
    """F3: single button attached to a fresh media-save acknowledgment —
    the deterministic ("no model judgment") twin of the harness's
    importance-recognition protocol. Token-based (see media.keep_token),
    not index-based, since this isn't a browsed list."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐ Keep", callback_data=f"ibx_keep_{keep_token(path)}"),
    ]])


def kb_outbox_categories(counts: dict):
    rows = []
    for cat, label in OUTBOX_CAT_META:
        n = counts.get(cat, 0)
        if n:
            rows.append([InlineKeyboardButton(
                f"{label} ({n})", callback_data=f"obx_cat_{cat}_0",
            )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_outbox_list(category: str, items: list, offset: int):
    """One row per file: [ name (size) | 📤 send | 🗑 delete ]."""
    rows = []
    page = items[offset:offset + INBOX_PAGE]
    for i, item in enumerate(page):
        idx = offset + i
        name = item["name"]
        # 18 chars: this row holds three buttons (STYLE.md R4/R17).
        label = name if len(name) <= 18 else name[:17] + "…"
        send_cb = f"obx_send_{category}_{idx}"
        rows.append([
            InlineKeyboardButton(
                f"{label} ({format_file_size(item['size'])})", callback_data=send_cb),
            InlineKeyboardButton("📤", callback_data=send_cb),
            InlineKeyboardButton("🗑", callback_data=f"obx_del_{category}_{idx}"),
        ])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            "◀ Previous", callback_data=f"obx_cat_{category}_{max(0, offset - INBOX_PAGE)}"))
    if offset + INBOX_PAGE < len(items):
        nav.append(InlineKeyboardButton(
            "Next ▶", callback_data=f"obx_cat_{category}_{offset + INBOX_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Categories", callback_data="menu_outbox"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_schedules(items: list):
    """Row per schedule: [state · title] toggles it, then [▶ run | 🗑] underneath."""
    rows = []
    for s in items:
        # One line per row: the panel text above already carries "next run".
        state = "✅" if s.get("enabled") else "⏸"
        title = s.get("title", "")[:28]
        rows.append([InlineKeyboardButton(
            f"{state} {title}",
            callback_data=f"sched_toggle_{s['id']}",
        )])
        rows.append([
            InlineKeyboardButton("▶ Run now", callback_data=f"sched_run_{s['id']}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"sched_del_{s['id']}"),
        ])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


# Phase B1 (PLAN.md §9/B1 step 4): the /tasks board. Live jobs get a stop
# button, finished ones a retry — nothing else, so the board still fits one
# phone screen (R18) once a few jobs have run.
TASKS_PAGE = 4


def kb_tasks(running: list, queued: list, finished: list):
    """One row per live job (stop), then one row per recent finished job
    (run again), then the nav row."""
    rows = []
    for t in (running + queued)[:TASKS_PAGE]:
        rows.append([InlineKeyboardButton(
            f"🗑 Stop {(t.get('title') or '')[:16]}",
            callback_data=f"task_stop_{t['id']}",
        )])
    for t in finished[:TASKS_PAGE]:
        rows.append([InlineKeyboardButton(
            f"▶ Again: {(t.get('title') or '')[:14]}",
            callback_data=f"task_retry_{t['id']}",
        )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_task_confirm(pid: str):
    """The agent proposed background work — one tap starts it, one dismisses
    it. A card attached to a message carries no Close (R20)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶ Run it", callback_data=f"bgt_ok_{pid}"),
        InlineKeyboardButton("Not now", callback_data=f"bgt_no_{pid}"),
    ]])


def kb_task_retry(tid: str):
    """Attached to a failed job's notice — one action, no Close (R20)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶ Try again", callback_data=f"task_retry_{tid}"),
    ]])


# Phase S (PLAN.md §11): the skills panel. One row per skill opens it; the
# on/off decision lives on the detail screen, so the list stays readable on
# a phone once a few skills exist (R18).
SKILLS_PAGE = 6


def kb_skill_confirm(pid: str):
    """The agent offered to save a skill — one tap saves it, one drops it.
    A card attached to a message carries no Close (R20)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💾 Save it", callback_data=f"skp_ok_{pid}"),
        InlineKeyboardButton("No thanks", callback_data=f"skp_no_{pid}"),
    ]])


def kb_skills(skills: list):
    """The /skills list — one row per managed skill, opening its detail."""
    rows = []
    for s in skills[:SKILLS_PAGE]:
        icon = {"ok": "✅", "unapproved": "⏸", "disabled": "🚫",
                "changed": "⚠️"}.get(s.get("state"), "•")
        rows.append([InlineKeyboardButton(
            f"{icon} {(s.get('name') or s.get('slug') or '')[:24]}",
            callback_data=f"skill_view_{s['slug']}",
        )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_skill_detail(slug: str, state: str):
    """One skill: switch it on (approve the bytes as they are now) or off.
    An approved-and-unchanged skill offers only 'off', so the owner is never
    asked to re-approve something that hasn't moved."""
    row = []
    if state != "ok":
        row.append(InlineKeyboardButton("✅ Switch on",
                                        callback_data=f"skill_ok_{slug}"))
    if state in ("ok", "changed"):
        row.append(InlineKeyboardButton("🚫 Switch off",
                                        callback_data=f"skill_off_{slug}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton("◀ Back", callback_data="menu_skills"),
                 _close_btn()])
    return InlineKeyboardMarkup(rows)
