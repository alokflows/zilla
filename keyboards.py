# ============================================================
#  KEYBOARDS — all inline-menu / button builders
# ============================================================
#  Pure UI layer: every function here turns plain data (counts,
#  item lists, the current model, the user id) into a Telegram
#  InlineKeyboardMarkup. No business logic, no network I/O.
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
)
from media import format_file_size

# Injected by bot.py at startup (see module docstring).
auth = None

# Idle-reaper cycle options shown in Settings.
_IDLE_OPTIONS = [
    (120, "2 min — Fast"),
    (180, "3 min — Default"),
    (300, "5 min — Patient"),
    (0, "No reaper"),
]

# Inbox/Outbox pagination + category metadata.
INBOX_PAGE = 10
INBOX_CAT_META = [
    ("images", "📷 Images"),
    ("audio", "🎵 Audio"),
    ("video", "🎬 Video"),
    ("documents", "📄 Documents"),
]
# Outbox (agent-produced files) — same UX as the Inbox, but no audio.
OUTBOX_CAT_META = [
    ("images", "📷 Images"),
    ("video", "🎬 Video"),
    ("documents", "📄 Documents"),
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
    return f"{val}s"


def _fmt_next(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%a %d %b %H:%M")


def kb_menu(uid: int = 0):
    is_admin = bool(auth and auth.can(uid, "admin"))
    rows = [
        [InlineKeyboardButton("📁 Sessions", callback_data="menu_sessions"),
         InlineKeyboardButton("📥 Inbox", callback_data="menu_inbox")],
        [InlineKeyboardButton("📤 Outbox", callback_data="menu_outbox")],
    ]
    if is_admin:
        # ⏰ Schedules previously had NO menu entry (command-only) — added here.
        rows.append([
            InlineKeyboardButton("⏰ Schedules", callback_data="menu_schedules"),
            InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
        ])
        model_row = [InlineKeyboardButton("🌐 Browse", callback_data="menu_browse")]
        if _can_change_model(uid):
            model_row.insert(0, InlineKeyboardButton("🤖 Model", callback_data="menu_model"))
        rows.append(model_row)
    rows.append([
        InlineKeyboardButton("🖥️ Status", callback_data="menu_status"),
        InlineKeyboardButton("🩺 Health", callback_data="menu_health"),
    ])
    if auth and auth.is_owner(uid):
        rows.append([InlineKeyboardButton("👥 Users", callback_data="menu_users")])
    rows.append([_close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_sessions(all_sessions: dict, active: str):
    buttons = []
    for name, info in all_sessions.items():
        marker = " ◀" if name == active else ""
        msgs = info.get("messages", 0)
        # Switch on the left, delete (🗑) on the right of the same row.
        buttons.append([
            InlineKeyboardButton(
                f"{name}{marker} ({msgs} msgs)",
                callback_data=f"sess_switch_{name}",
            ),
            InlineKeyboardButton("🗑", callback_data=f"sess_delete_{name}"),
        ])
    buttons.append([
        InlineKeyboardButton("➕ New", callback_data="sess_new"),
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
        _close_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_session_delete(name: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, delete", callback_data=f"sess_confirm_del_{name}"),
         InlineKeyboardButton("❌ Cancel", callback_data="sess_list")],
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
    other = "claude" if get_backend() == "agy" else "agy"
    buttons.append([
        InlineKeyboardButton("✏️ Custom…", callback_data="model_custom"),
        InlineKeyboardButton(f"🧠 Use {other}", callback_data="model_switch_backend"),
    ])
    buttons.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(buttons)


def kb_settings(uid: int = 0):
    auto_photo = get_setting("auto_describe_photos", False)
    rows = [
        [InlineKeyboardButton(
            f"📸 Auto-analyze photos: {'ON' if auto_photo else 'OFF'}",
            callback_data="set_toggle_photo",
        )],
    ]
    if auth and auth.can(uid, "admin"):
        idle_kill = get_idle_kill_after()
        rows.append([InlineKeyboardButton(
            f"⏱️ Idle reaper: {_idle_label(idle_kill)}",
            callback_data="set_cycle_idle",
        )])
    if auth and auth.can(uid, "admin"):
        catchup = get_setting("schedule_catchup", True)
        rows.append([InlineKeyboardButton(
            f"⏰ Catch up missed schedules: {'ON' if catchup else 'OFF'}",
            callback_data="set_toggle_catchup",
        )])
    if auth and auth.is_owner(uid):
        admins_model = get_setting("admins_can_change_model", True)
        rows.append([InlineKeyboardButton(
            f"🤖 Admins can change model: {'ON' if admins_model else 'OFF'}",
            callback_data="set_toggle_admin_model",
        )])
        rows.append([InlineKeyboardButton(
            f"🧠 Backend: {get_backend()}  (tap to switch)",
            callback_data="set_toggle_backend",
        )])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()],
    ])


def kb_error():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Retry", callback_data="err_retry"),
         InlineKeyboardButton("🤖 Change Model", callback_data="err_model")],
    ])


def kb_users(users: dict):
    buttons = []
    for uid_int, info in users.items():
        name = info.get("name") or f"User {uid_int}"
        role = info.get("role", "admin")
        buttons.append([InlineKeyboardButton(
            f"[{role}] {name}", callback_data=f"user_detail_{uid_int}",
        )])
    buttons.append([
        InlineKeyboardButton("➕ Add User", callback_data="user_add_start"),
        InlineKeyboardButton("◀ Menu", callback_data="menu_back"),
        _close_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def kb_user_detail(target_id: int, role: str = "admin"):
    # Role toggle: admins have full unattended access; "limited" users can chat
    # but every request waits for the owner's approval (Approval mode).
    if role == "limited":
        toggle = InlineKeyboardButton(
            "🔓 Give full access", callback_data=f"user_role_admin_{target_id}")
    else:
        toggle = InlineKeyboardButton(
            "🔒 Put in Approval mode", callback_data=f"user_role_limited_{target_id}")
    return InlineKeyboardMarkup([
        [toggle],
        [InlineKeyboardButton("🗑️ Remove", callback_data=f"user_remove_{target_id}"),
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
        label = name if len(name) <= 24 else name[:21] + "…"
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
            "⬅️ Prev", callback_data=f"ibx_cat_{category}_{max(0, offset - INBOX_PAGE)}"))
    if offset + INBOX_PAGE < len(items):
        nav.append(InlineKeyboardButton(
            "More ➡️", callback_data=f"ibx_cat_{category}_{offset + INBOX_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Categories", callback_data="menu_inbox"), _close_btn()])
    return InlineKeyboardMarkup(rows)


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
        label = name if len(name) <= 24 else name[:21] + "…"
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
            "⬅️ Prev", callback_data=f"obx_cat_{category}_{max(0, offset - INBOX_PAGE)}"))
    if offset + INBOX_PAGE < len(items):
        nav.append(InlineKeyboardButton(
            "More ➡️", callback_data=f"obx_cat_{category}_{offset + INBOX_PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀ Categories", callback_data="menu_outbox"), _close_btn()])
    return InlineKeyboardMarkup(rows)


def kb_schedules(items: list):
    """Row per schedule: [▶ toggle/title · next] then [▶️ run | 🗑] underneath."""
    rows = []
    for s in items:
        state = "✅" if s.get("enabled") else "⏸"
        title = s.get("title", "")[:24]
        rows.append([InlineKeyboardButton(
            f"{state} {title} · {_fmt_next(s.get('next_run'))}",
            callback_data=f"sched_toggle_{s['id']}",
        )])
        rows.append([
            InlineKeyboardButton("▶️ Run now", callback_data=f"sched_run_{s['id']}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"sched_del_{s['id']}"),
        ])
    rows.append([InlineKeyboardButton("◀ Menu", callback_data="menu_back"), _close_btn()])
    return InlineKeyboardMarkup(rows)
