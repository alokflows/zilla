# ============================================================
#  TESTS — Phase H2: health probes + assisted re-login
#  (PLAN.md §6/H2 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - zilla/health.py: each probe with injected failures (disk low, db not
#      writable, binary missing, agy/claude login broken), per-kind TTL
#      caching, the alert cooldown (fires once, stays silent while still
#      broken, resets on recovery), recovery_instructions (the honest
#      ceiling — detect + instructions, no login automation), and
#      beat_flag_lines() feeding H1's beat prompt.
#    - zilla/core.py: health_probes_enabled defaults False (no real
#      subprocess ever spawned by a test-constructed ZillaCore);
#      _health_tick's self-heal-then-alert decision logic against a fully
#      mocked probe round; start()/stop() only creates/tears down the
#      health task when the flag is explicitly turned on.
#    - zilla/heartbeat.py: build_beat_prompt's flags= prefix.
#
#  Run:  python test_health.py
#  Exit code 0 = all passed, 1 = something failed.
#
#  No real `agy`/`claude` subprocess is ever invoked — every probe's
#  compute() closure is exercised directly with monkeypatched inputs, or
#  the probe function itself is monkeypatched at the module-attribute
#  level for the core.py integration tests (same pattern test_core.py's
#  _patched_health uses for health_report()).
# ============================================================

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


# ── Isolate config BEFORE anything touches the store ──
_tmpdir = tempfile.mkdtemp(prefix="zilla_h2_cfg_")
_fake_agy = os.path.join(_tmpdir, "agy_settings.json")
with open(_fake_agy, "w", encoding="utf-8") as f:
    json.dump({"model": "Gemini 3.1 Pro (High)"}, f)
os.environ["AGY_SETTINGS_FILE"] = _fake_agy
os.environ["BACKEND"] = "agy"

import zilla.config as config  # noqa: E402
config.DB_FILE = os.path.join(_tmpdir, "zilla_test.db")
config.SETTINGS_FILE = config.DB_FILE
config._settings_cache = None

import zilla.health as health  # noqa: E402
import zilla.heartbeat as heartbeat  # noqa: E402
import zilla.core as zcore  # noqa: E402
from zilla.core import ZillaCore, Alert  # noqa: E402
from zilla.sessions import SessionManager  # noqa: E402
from zilla.users import AuthManager  # noqa: E402

OWNER = 111


def _fresh_core(tag: str) -> ZillaCore:
    sessions = SessionManager(os.path.join(_tmpdir, f"sessions_{tag}.json"))
    auth = AuthManager(os.path.join(_tmpdir, f"users_{tag}.json"), OWNER)
    return ZillaCore(sessions=sessions, auth=auth)


async def _drain(q: asyncio.Queue, n: int, timeout: float = 2.0):
    out = []

    async def go():
        while len(out) < n:
            out.append(await q.get())
    try:
        await asyncio.wait_for(go(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return out


# ── 1. probe_disk ──

def test_probe_disk_pass_and_fail():
    print("\n[1] probe_disk() — real disk_usage, injected threshold")
    health.reset_cache()
    ok_low_bar = health.probe_disk(base=_tmpdir, min_free_mb=1, force=True)
    check("a 1 MB bar passes on any real disk", ok_low_bar["ok"] is True, ok_low_bar)

    huge_bar = health.probe_disk(base=_tmpdir, min_free_mb=10**9, force=True)
    check("an absurd 1 EB bar fails", huge_bar["ok"] is False, huge_bar)
    check("failure detail names the shortfall", "MB free" in huge_bar["detail"], huge_bar)


def test_probe_disk_bad_path_never_raises():
    print("\n[1b] probe_disk() — nonexistent path fails cleanly, never raises")
    health.reset_cache()
    res = health.probe_disk(base="/definitely/does/not/exist/zzz", force=True)
    check("ok is False", res["ok"] is False, res)


# ── 2. probe_db_writable ──

def test_probe_db_writable_pass_and_fail():
    print("\n[2] probe_db_writable() — writable dir passes, missing dir fails")
    health.reset_cache()
    db_path = os.path.join(_tmpdir, "some.db")
    ok = health.probe_db_writable(db_path, force=True)
    check("writable temp dir passes", ok["ok"] is True, ok)

    bad = health.probe_db_writable("/definitely/does/not/exist/zzz/x.db", force=True)
    check("nonexistent dir fails, never raises", bad["ok"] is False, bad)


# ── 3. probe_backend_path ──

def test_probe_backend_path():
    print("\n[3] probe_backend_path() — existence check only, no subprocess")
    health.reset_cache()
    real = health.probe_backend_path("fakebin", __file__, force=True)
    check("an existing file path passes", real["ok"] is True, real)

    missing = health.probe_backend_path("fakebin", "/no/such/binary/here", force=True)
    check("a missing path fails", missing["ok"] is False, missing)

    unset = health.probe_backend_path("fakebin", None, force=True)
    check("None path fails cleanly", unset["ok"] is False, unset)


# ── 4. probe_agy_login (monkeypatched, no real subprocess) ──

def test_probe_agy_login_injected():
    print("\n[4] probe_agy_login() — injected reachable / unreachable")
    health.reset_cache()
    old = config.agy_reachable
    try:
        config.agy_reachable = lambda: True
        ok = health.probe_agy_login(force=True)
        check("reachable -> ok", ok["ok"] is True, ok)

        health.reset_cache()
        config.agy_reachable = lambda: False
        bad = health.probe_agy_login(force=True)
        check("unreachable -> not ok", bad["ok"] is False, bad)
        check("detail mentions logged out", "logged out" in bad["detail"], bad)
    finally:
        config.agy_reachable = old


# ── 5. probe_claude_login (subprocess.run monkeypatched — NEVER real claude) ──

class _FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_probe_claude_login_injected_success():
    print("\n[5] probe_claude_login() — injected successful ping, TTL respected")
    health.reset_cache()
    calls = []
    old = subprocess.run

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _FakeCompleted(stdout=json.dumps({"result": "pong", "is_error": False}))

    subprocess.run = fake_run
    try:
        res1 = health.probe_claude_login(force=True, claude_path="/fake/claude")
        check("ping success -> ok", res1["ok"] is True, res1)
        check("one subprocess call made", len(calls) == 1, calls)

        # Second call WITHOUT force, within the 6h TTL, must NOT re-invoke.
        res2 = health.probe_claude_login(force=False, claude_path="/fake/claude")
        check("cached result reused inside the 6h TTL — no second subprocess call",
              len(calls) == 1, calls)
        check("cached result matches", res2["ok"] is True, res2)
    finally:
        subprocess.run = old


def test_probe_claude_login_injected_failure_variants():
    print("\n[5b] probe_claude_login() — is_error, timeout, missing binary")
    old = subprocess.run

    def fake_run_error(cmd, **kw):
        return _FakeCompleted(stdout=json.dumps({"is_error": True, "error": "session expired"}))

    subprocess.run = fake_run_error
    try:
        health.reset_cache()
        res = health.probe_claude_login(force=True, claude_path="/fake/claude")
        check("is_error response -> not ok", res["ok"] is False, res)
        check("detail carries the CLI's own error text",
              "session expired" in res["detail"], res)
    finally:
        subprocess.run = old

    def fake_run_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 15))

    subprocess.run = fake_run_timeout
    try:
        health.reset_cache()
        res = health.probe_claude_login(force=True, claude_path="/fake/claude")
        check("timeout -> not ok, never raises", res["ok"] is False, res)
    finally:
        subprocess.run = old

    def fake_run_missing(cmd, **kw):
        raise FileNotFoundError()

    subprocess.run = fake_run_missing
    try:
        health.reset_cache()
        res = health.probe_claude_login(force=True, claude_path="/fake/claude")
        check("missing binary -> not ok, never raises", res["ok"] is False, res)
    finally:
        subprocess.run = old


# ── 6. Alert cooldown ──

def test_alert_cooldown():
    print("\n[6] should_alert/mark_alerted/clear_alert — 6h cooldown, resets on recovery")
    health.reset_cache()
    now = 1_000_000.0
    check("first failure always alerts", health.should_alert("disk", now=now) is True)
    health.mark_alerted("disk", now=now)
    check("immediately after -> still within cooldown, no repeat",
          health.should_alert("disk", now=now + 60) is False)
    check("just under 6h -> still silent",
          health.should_alert("disk", now=now + health.ALERT_COOLDOWN - 1) is False)
    check("at/after 6h -> alerts again",
          health.should_alert("disk", now=now + health.ALERT_COOLDOWN) is True)

    health.clear_alert("disk")
    check("recovery clears the cooldown state", health.is_alerted("disk") is False)
    check("a NEW failure right after recovery alerts immediately (no stale wait)",
          health.should_alert("disk", now=now + health.ALERT_COOLDOWN + 1) is True)


def test_alert_cooldown_is_per_kind():
    print("\n[6b] cooldown state is independent per probe kind")
    health.reset_cache()
    health.mark_alerted("agy_login", now=1000.0)
    check("a different kind is unaffected", health.should_alert("claude_login", now=1000.0) is True)


# ── 7. recovery_instructions — the honest ceiling ──

def test_recovery_instructions_no_automation_language():
    print("\n[7] recovery_instructions() — detect + instructions only, never 'automatically logs in'")
    for kind in ("agy_login", "claude_login", "agy_path", "claude_path",
                 "db_writable", "disk"):
        text = health.recovery_instructions(kind)
        check(f"{kind}: non-empty plain-language instructions", bool(text.strip()), text)
    unknown = health.recovery_instructions("some_future_probe")
    check("unknown kind still returns a safe generic fallback",
          "doctor" in unknown, unknown)


# ── 8. beat_flag_lines — feeds H1's beat prompt ──

def test_beat_flag_lines():
    print("\n[8] beat_flag_lines() — one line per currently-alerted probe, sorted, stable format")
    health.reset_cache()
    check("nothing alerted -> empty list", health.beat_flag_lines() == [])
    health.mark_alerted("claude_login")
    health.mark_alerted("agy_login")
    lines = health.beat_flag_lines()
    check("one line per alerted kind", len(lines) == 2, lines)
    check("sorted for determinism", lines == sorted(lines), lines)
    check("each line names its kind and 'already DM'd owner'",
          all("already DM'd owner" in ln for ln in lines), lines)

    health.clear_alert("agy_login")
    check("cleared kind drops out of the flag list",
          health.beat_flag_lines() == ["System flag: claude_login — already DM'd owner."],
          health.beat_flag_lines())
    health.clear_alert("claude_login")


def test_build_beat_prompt_with_flags():
    print("\n[8b] heartbeat.build_beat_prompt(flags=...) — prepended, one per line")
    from datetime import datetime
    now = datetime(2026, 7, 18, 9, 0)
    p = heartbeat.build_beat_prompt(now, None, "PDT",
                                    flags=["System flag: disk — already DM'd owner."])
    check("flag line appears before the time sentence",
          p.index("System flag: disk") < p.index("It is 2026-07-18"), p)
    check("flag line is followed by its own newline", "owner.\nIt is" in p, p)

    p_none = heartbeat.build_beat_prompt(now, None, "PDT")
    check("no flags -> prompt starts with 'It is' as before (H1 unchanged)",
          p_none.startswith("It is "), p_none)


# ── 9. core.py — health_probes_enabled gating + _health_tick logic ──

def test_health_probes_disabled_by_default():
    print("\n[9] ZillaCore.health_probes_enabled defaults False")
    core = _fresh_core("default_flag")
    check("off by default", core.health_probes_enabled is False)


def test_start_stop_does_not_spawn_health_task_when_disabled():
    print("\n[10] start()/stop() — no health task when the flag is off (no real subprocess risk)")
    core = _fresh_core("no_health")

    async def run():
        await core.start()
        check("no health task created", core._health_task is None)
        await core.stop()
        check("still None after stop", core._health_task is None)

    asyncio.run(run())


def test_start_stop_spawns_and_tears_down_health_task_when_enabled():
    print("\n[11] start()/stop() — health task created/torn down when explicitly enabled")
    core = _fresh_core("with_health")
    core.health_probes_enabled = True

    # Prevent any real probe work during this lifecycle test.
    async def fake_tick():
        await asyncio.sleep(3600)

    async def run():
        core._health_tick = fake_tick
        await core.start()
        check("health task created", core._health_task is not None)
        await asyncio.sleep(0.05)
        check("health task alive", not core._health_task.done())
        await core.stop()
        check("health task cleared after stop", core._health_task is None)

    asyncio.run(run())


def test_health_tick_alerts_once_then_respects_cooldown():
    print("\n[12] _health_tick() — failing probe alerts once, second tick (same failure) stays silent")
    health.reset_cache()
    core = _fresh_core("tick_alert")
    sink = asyncio.Queue()
    core.subscribe(sink)

    def fake_run_probes(active_backend, db_path, force=False):
        return {"agy_login": {"ok": False, "detail": "agy installed but not responding"}}

    old = health.run_probes
    health.run_probes = fake_run_probes
    try:
        async def run():
            await core._health_tick()
            first = await _drain(sink, 1, timeout=0.5)
            await core._health_tick()  # same failure again, still within cooldown
            second = await _drain(sink, 1, timeout=0.3)
            return first, second

        first, second = asyncio.run(run())
        check("first failing tick broadcasts exactly one Alert", len(first) == 1, first)
        check("it is an Alert naming the probe detail",
              first and isinstance(first[0], Alert) and "agy_login" in first[0].text, first)
        check("second tick (still broken, within cooldown) broadcasts nothing",
              second == [], second)
    finally:
        health.run_probes = old
        health.reset_cache()


def test_health_tick_recovery_clears_and_realerts_on_new_failure():
    print("\n[13] _health_tick() — recovery clears cooldown; a later NEW failure alerts again")
    health.reset_cache()
    core = _fresh_core("tick_recover")
    sink = asyncio.Queue()
    core.subscribe(sink)

    state = {"ok": False}

    def fake_run_probes(active_backend, db_path, force=False):
        return {"claude_login": {"ok": state["ok"], "detail": "probe result"}}

    old = health.run_probes
    health.run_probes = fake_run_probes
    try:
        async def run():
            await core._health_tick()
            first = await _drain(sink, 1, timeout=0.5)
            state["ok"] = True
            await core._health_tick()  # recovers -> clears alert state
            recovered_alerted = health.is_alerted("claude_login")
            state["ok"] = False
            await core._health_tick()  # a fresh failure right after recovery
            second = await _drain(sink, 1, timeout=0.5)
            return first, recovered_alerted, second

        first, recovered_alerted, second = asyncio.run(run())
        check("initial failure alerts", len(first) == 1, first)
        check("recovery clears the alerted state", recovered_alerted is False)
        check("a fresh failure right after recovery alerts again immediately",
              len(second) == 1, second)
    finally:
        health.run_probes = old
        health.reset_cache()


def test_health_tick_disk_self_heals_silently():
    print("\n[14] _health_tick() — disk failure that self-heals never alerts")
    health.reset_cache()
    core = _fresh_core("tick_disk_heal")
    sink = asyncio.Queue()
    core.subscribe(sink)

    def fake_run_probes(active_backend, db_path, force=False):
        return {"disk": {"ok": False, "detail": "1 MB free (need 500)"}}

    async def fake_self_heal():
        return True  # pretend the GC freed enough space

    old = health.run_probes
    health.run_probes = fake_run_probes
    core._self_heal_disk = fake_self_heal
    try:
        async def run():
            await core._health_tick()
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("a self-healed disk failure never alerts the owner", events == [], events)
        check("self-heal also clears the alert cooldown state",
              health.is_alerted("disk") is False)
    finally:
        health.run_probes = old
        health.reset_cache()


def test_health_tick_disk_alerts_when_self_heal_insufficient():
    print("\n[15] _health_tick() — disk failure that self-heal can't fix DOES alert")
    health.reset_cache()
    core = _fresh_core("tick_disk_nofix")
    sink = asyncio.Queue()
    core.subscribe(sink)

    def fake_run_probes(active_backend, db_path, force=False):
        return {"disk": {"ok": False, "detail": "1 MB free (need 500)"}}

    async def fake_self_heal():
        return False  # GC ran but didn't free enough

    old = health.run_probes
    health.run_probes = fake_run_probes
    core._self_heal_disk = fake_self_heal
    try:
        async def run():
            await core._health_tick()
            return await _drain(sink, 1, timeout=0.5)

        events = asyncio.run(run())
        check("insufficient self-heal still alerts the owner", len(events) == 1, events)
    finally:
        health.run_probes = old
        health.reset_cache()


if __name__ == "__main__":
    tests = [
        test_probe_disk_pass_and_fail,
        test_probe_disk_bad_path_never_raises,
        test_probe_db_writable_pass_and_fail,
        test_probe_backend_path,
        test_probe_agy_login_injected,
        test_probe_claude_login_injected_success,
        test_probe_claude_login_injected_failure_variants,
        test_alert_cooldown,
        test_alert_cooldown_is_per_kind,
        test_recovery_instructions_no_automation_language,
        test_beat_flag_lines,
        test_build_beat_prompt_with_flags,
        test_health_probes_disabled_by_default,
        test_start_stop_does_not_spawn_health_task_when_disabled,
        test_start_stop_spawns_and_tears_down_health_task_when_enabled,
        test_health_tick_alerts_once_then_respects_cooldown,
        test_health_tick_recovery_clears_and_realerts_on_new_failure,
        test_health_tick_disk_self_heals_silently,
        test_health_tick_disk_alerts_when_self_heal_insufficient,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    shutil.rmtree(_tmpdir, ignore_errors=True)
    sys.exit(1 if _failed else 0)
