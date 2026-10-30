"""Cross-platform process liveness probe.

``os.kill(pid, 0)`` is the POSIX idiom for "does this PID exist?". On Windows the
same call is translated to ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)``: the
PID is interpreted as a *process group id*, so it delivers CTRL+C to the whole
console group — including the process that asked. That is why running the test
suite in a Windows console could be interrupted by a stray ``KeyboardInterrupt``
while a lease check ran.
"""

from __future__ import annotations

import ctypes
import os

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def process_is_alive(pid: int) -> bool:
    """Return whether ``pid`` refers to a live process, without signalling it."""

    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
