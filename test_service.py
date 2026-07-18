# ============================================================
#  TESTS — Phase H3: systemd Linux service deployment
#  (PLAN.md §6/H3 "Accept:" criteria)
# ============================================================
#  Deterministic, no-network tests for:
#    - install.py: systemd_unit_content() golden test (exact expected unit
#      text — ExecStart/WorkingDirectory/Restart=on-failure/WantedBy);
#      write_service() writes to an isolated tmp path (never the real
#      ~/.config/systemd/user) with systemctl calls mocked, returns 0 on
#      success and 1 on systemctl failure/missing, never touches a real
#      system.
#    - zilla/doctor.py: check_systemd_service() — applicable=False off
#      Linux (the real, unmocked case on this dev machine); on a
#      monkeypatched IS_LINUX=True, parses is-active/is-enabled output
#      (active+enabled, inactive, disabled, not-installed) and never
#      raises on a missing systemctl binary or a timeout.
#
#  Live-only accept criterion NOT covered here (owner's call, same
#  deferral category as every prior phase's live-smoke items): "reboot ->
#  bot up, missed schedules caught up" needs a real Linux box with the
#  service actually enabled — the existing reconcile_startup catch-up
#  logic itself is already covered by test_heartbeat.py/test_fixes.py.
#
#  Run:  python test_service.py
# ============================================================

import os
import subprocess
import sys
import tempfile

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


import install  # noqa: E402
import zilla.doctor as zdoctor  # noqa: E402
import zilla.platform_compat as platform_compat  # noqa: E402


# ── 1. systemd_unit_content — golden test ──

def test_unit_content_golden():
    print("\n[1] systemd_unit_content() — exact expected unit text")
    text = install.systemd_unit_content("/usr/bin/python3", "/home/alok/zilla")
    expected = (
        "[Unit]\n"
        "Description=Zilla Telegram bot\n"
        "\n"
        "[Service]\n"
        "ExecStart=/usr/bin/python3 /home/alok/zilla/run_background.py\n"
        "WorkingDirectory=/home/alok/zilla\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    check("exact golden match", text == expected, text)


def test_unit_content_never_always_restart():
    print("\n[1b] systemd_unit_content() — Restart=on-failure, never Restart=always")
    text = install.systemd_unit_content("/x/python3", "/x/zilla")
    check("Restart=on-failure present", "Restart=on-failure" in text, text)
    check("Restart=always NOT present (would fight zilla.stop's clean exit)",
          "Restart=always" not in text, text)
    check("WantedBy=default.target present (user-unit convention)",
          "WantedBy=default.target" in text, text)


# ── 2. write_service — isolated, systemctl mocked ──

class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def check_returncode(self):
        if self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, "systemctl")


def test_write_service_success_isolated():
    print("\n[2] write_service() — writes unit to an isolated tmp path, systemctl mocked ok")
    tmpdir = tempfile.mkdtemp(prefix="zilla_h3_svc_")
    old_dir, old_path = install.SYSTEMD_UNIT_DIR, install.SYSTEMD_UNIT_PATH
    old_run = subprocess.run
    calls = []
    try:
        install.SYSTEMD_UNIT_DIR = tmpdir
        install.SYSTEMD_UNIT_PATH = os.path.join(tmpdir, "zilla.service")

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _FakeCompleted(0)

        subprocess.run = fake_run
        rc = install.write_service()
        check("returns 0 on success", rc == 0, rc)
        check("unit file actually written", os.path.exists(install.SYSTEMD_UNIT_PATH))
        with open(install.SYSTEMD_UNIT_PATH, encoding="utf-8") as f:
            content = f.read()
        check("written content matches systemd_unit_content()",
              content == install.systemd_unit_content(sys.executable, install.BASE), content)
        check("daemon-reload called", ["systemctl", "--user", "daemon-reload"] in calls, calls)
        check("enable --now called",
              ["systemctl", "--user", "enable", "--now", "zilla.service"] in calls, calls)
        check("never touched the REAL ~/.config/systemd/user path",
              not os.path.exists(old_path) or open(old_path, encoding="utf-8").read() != content
              if os.path.exists(old_path) else True)
    finally:
        subprocess.run = old_run
        install.SYSTEMD_UNIT_DIR, install.SYSTEMD_UNIT_PATH = old_dir, old_path
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)


def test_write_service_missing_systemctl():
    print("\n[3] write_service() — missing systemctl binary returns 1, never raises")
    tmpdir = tempfile.mkdtemp(prefix="zilla_h3_svc_missing_")
    old_dir, old_path = install.SYSTEMD_UNIT_DIR, install.SYSTEMD_UNIT_PATH
    old_run = subprocess.run
    try:
        install.SYSTEMD_UNIT_DIR = tmpdir
        install.SYSTEMD_UNIT_PATH = os.path.join(tmpdir, "zilla.service")

        def fake_run_missing(cmd, **kw):
            raise FileNotFoundError()

        subprocess.run = fake_run_missing
        rc = install.write_service()
        check("returns 1, never raises", rc == 1, rc)
        check("unit file still written even though systemctl is missing "
              "(a later manual `systemctl --user enable --now` can recover)",
              os.path.exists(install.SYSTEMD_UNIT_PATH))
    finally:
        subprocess.run = old_run
        install.SYSTEMD_UNIT_DIR, install.SYSTEMD_UNIT_PATH = old_dir, old_path
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)


def test_write_service_systemctl_failure():
    print("\n[4] write_service() — systemctl returning nonzero returns 1, never raises")
    tmpdir = tempfile.mkdtemp(prefix="zilla_h3_svc_fail_")
    old_dir, old_path = install.SYSTEMD_UNIT_DIR, install.SYSTEMD_UNIT_PATH
    old_run = subprocess.run
    try:
        install.SYSTEMD_UNIT_DIR = tmpdir
        install.SYSTEMD_UNIT_PATH = os.path.join(tmpdir, "zilla.service")

        def fake_run_fail(cmd, **kw):
            if kw.get("check"):
                raise subprocess.CalledProcessError(1, cmd)
            return _FakeCompleted(1)

        subprocess.run = fake_run_fail
        rc = install.write_service()
        check("returns 1 on systemctl failure", rc == 1, rc)
    finally:
        subprocess.run = old_run
        install.SYSTEMD_UNIT_DIR, install.SYSTEMD_UNIT_PATH = old_dir, old_path
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)


# ── 3. doctor.check_systemd_service ──

def test_check_service_not_applicable_off_linux():
    print("\n[5] check_systemd_service() — applicable=False on this (non-Linux) dev machine")
    res = zdoctor.check_systemd_service()
    if platform_compat.IS_LINUX:
        check("skipped: this check machine IS Linux, not the target case", True)
    else:
        check("applicable is False", res["applicable"] is False, res)
        check("detail explains why", "Linux" in res["detail"], res)


def test_check_service_parsing_on_forced_linux():
    print("\n[6] check_systemd_service() — output parsing, forced IS_LINUX=True")
    old_linux = platform_compat.IS_LINUX
    old_run = subprocess.run
    try:
        platform_compat.IS_LINUX = True

        def fake_active_enabled(cmd, **kw):
            if "is-active" in cmd:
                return _FakeCompletedOut("active\n")
            return _FakeCompletedOut("enabled\n")

        subprocess.run = fake_active_enabled
        res = zdoctor.check_systemd_service()
        check("active+enabled -> both True", res["active"] and res["enabled"], res)

        def fake_inactive_disabled(cmd, **kw):
            if "is-active" in cmd:
                return _FakeCompletedOut("inactive\n")
            return _FakeCompletedOut("disabled\n")

        subprocess.run = fake_inactive_disabled
        res2 = zdoctor.check_systemd_service()
        check("inactive+disabled -> both False", not res2["active"] and not res2["enabled"], res2)

        def fake_not_installed(cmd, **kw):
            return _FakeCompletedOut("")

        subprocess.run = fake_not_installed
        res3 = zdoctor.check_systemd_service()
        check("empty output (unit doesn't exist) -> not installed, no crash",
              res3["active"] is False and res3["enabled"] is False, res3)
        check("detail names the install command",
              "install.py --service" in res3["detail"], res3)
    finally:
        platform_compat.IS_LINUX = old_linux
        subprocess.run = old_run


def test_check_service_missing_systemctl_never_raises():
    print("\n[7] check_systemd_service() — missing systemctl / timeout never raises")
    old_linux = platform_compat.IS_LINUX
    old_run = subprocess.run
    try:
        platform_compat.IS_LINUX = True

        def fake_missing(cmd, **kw):
            raise FileNotFoundError()

        subprocess.run = fake_missing
        res = zdoctor.check_systemd_service()
        check("missing systemctl -> applicable True, active False, no crash",
              res["applicable"] is True and res["active"] is False, res)

        def fake_timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 3))

        subprocess.run = fake_timeout
        res2 = zdoctor.check_systemd_service()
        check("systemctl timeout -> no crash", res2["active"] is False, res2)
    finally:
        platform_compat.IS_LINUX = old_linux
        subprocess.run = old_run


def test_environment_report_includes_service():
    print("\n[8] environment_report() — 'service' key always present, format_report renders it")
    report = zdoctor.environment_report()
    check("'service' key present", "service" in report, report.keys())
    text = zdoctor.format_report(report)
    if report["service"].get("applicable"):
        check("service line rendered when applicable", "systemd service" in text, text)
    else:
        check("no service line rendered when not applicable (this dev machine)",
              "systemd service" not in text, text)


class _FakeCompletedOut:
    def __init__(self, stdout):
        self.stdout = stdout


if __name__ == "__main__":
    tests = [
        test_unit_content_golden,
        test_unit_content_never_always_restart,
        test_write_service_success_isolated,
        test_write_service_missing_systemctl,
        test_write_service_systemctl_failure,
        test_check_service_not_applicable_off_linux,
        test_check_service_parsing_on_forced_linux,
        test_check_service_missing_systemctl_never_raises,
        test_environment_report_includes_service,
    ]
    for t in tests:
        t()

    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
