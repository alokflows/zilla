"""Legacy import shim — module moved to zilla/ (Phase 1). Delete when nothing imports the old name."""
import sys as _sys
import zilla.platform_compat as _mod
_sys.modules[__name__] = _mod
