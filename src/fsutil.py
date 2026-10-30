"""Filesystem helpers shared by the low-level state stores.

``os.replace`` is atomic on POSIX and on Windows, but Windows still fails with
``PermissionError`` when another handle (a reader in the manager loop, an
antivirus scanner, an indexer) holds the destination without delete sharing.
Production code reads checkpoints and job state while workers rewrite them, so a
short retry loop is the difference between a spurious worker failure and a clean
hand-off.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_ATTEMPTS = 10
DEFAULT_DELAY_SECONDS = 0.02


def replace_with_retry(
    source: Path,
    destination: Path,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay: float = DEFAULT_DELAY_SECONDS,
) -> None:
    """``os.replace`` with bounded backoff on transient sharing violations."""

    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)


def unlink_with_retry(
    path: Path,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay: float = DEFAULT_DELAY_SECONDS,
) -> None:
    """Delete a file that a concurrent reader may still hold open.

    CPython opens files without ``FILE_SHARE_DELETE`` on Windows, so deleting a
    file the manager loop is reading raises ``WinError 32`` (a ``PermissionError``).
    The worker deletes its request/grant files on every stage transition, so the
    delete needs the same bounded retry as the replace.
    """

    for attempt in range(1, attempts + 1):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)
