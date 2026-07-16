"""Legacy import shim — module moved to zilla/ (Phase 1). Delete when nothing imports the old name."""
import sys as _sys
import zilla.schedule_parse as _mod
_sys.modules[__name__] = _mod
