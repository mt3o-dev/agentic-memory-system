"""Cross-process locking for the store *file* — the one thing SQLite cannot do for us.

SQLite already makes concurrent connections to one database safe: WAL gives many readers
alongside one writer, and ``busy_timeout`` turns contention into a wait rather than an
error. What no SQLite setting can survive is the file being **replaced out from under an
open connection**, which is exactly what git sync does when the tracked dump moves ahead
(``sync.restore_from_text``). Two mechanisms bite, and the second is the nasty one:

- the replacement has a different inode, so a live connection keeps reading and writing
  the file nobody can see any more, and its work vanishes at the next open;
- ``-wal`` / ``-shm`` sidecars are addressed **by filename, not by inode**, so the *old*
  database's WAL is inherited by the new file. SQLite replays those frames over pages
  they never belonged to. Observed outcomes range from a restore that silently does not
  happen (the old database's rows reappear through the WAL, and the rebuilt file is
  quietly discarded) to real B-tree damage — ``database disk image is malformed``,
  ``Rowid N out of order`` — which is how this was first reported.

So the rule this module enforces is narrow, structural, and the whole point:

    every live connection holds a SHARED lock;
    replacing the file requires an EXCLUSIVE one.

Concurrency *within* the file stays SQLite's job, unchanged — shared locks do not
serialize writers, and two agents writing at once is still a supported thing to do. The
exclusive lock exists only to make the destructive whole-file swap impossible while
anybody has the store open. When it cannot be taken, the caller **skips the replace and
says so**: a database that is merely out of date is a fixable inconvenience, and a
database that was swapped under a live connection is a corrupt one.

**Why a sidecar file and not the database itself.** A lock on the database would be a
lock on an inode that the very operation it guards is about to throw away — the next
process would lock the *new* file and see no conflict. ``<db>.lock`` is never replaced,
so every process locks the same object. It is created once and never deleted: unlinking
a lock file that another process still holds is the classic way to hand two processes
the same "exclusive" lock.

**Why ``flock`` and not a pid file.** The lock is released by the kernel when the fd
closes, so a killed process — ``kill -9``, a crashed GUI, a closed laptop lid — leaves
nothing stale behind. A pid file would need a liveness check, and every such check is a
race. The cost is that ``flock`` is POSIX; on a platform without ``fcntl``, or a
filesystem that refuses the call, locking degrades to a no-op and
``locking_available()`` reports false, rather than pretending. Set ``MEMORY_LOCK=0`` to
turn it off deliberately.
"""

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

LOCK_SUFFIX = ".lock"
_IN_MEMORY = (":memory:", "")

# Waiting out someone else's rebuild is the safe direction, so the shared timeout is
# generous: the alternative to waiting is connecting to a file mid-swap.
DEFAULT_WAIT = 10.0
# Waiting for a rebuild *slot* is the unsafe direction to be patient about — the common
# reason it is busy is a long-lived GUI server, which will not let go this decade. Fail
# fast, skip the rebuild, report it, and let the next open try again.
REPLACE_WAIT = 2.0
_POLL = 0.02

# Errnos that mean "this filesystem does not do flock", as opposed to "someone else holds
# it". Treated as locking being unavailable here, never as a lock being held.
_UNSUPPORTED = frozenset({errno.EINVAL, errno.ENOLCK, errno.ENOSYS, errno.EOPNOTSUPP})


class StoreBusy(RuntimeError):
    """The lock could not be taken before the timeout — someone else holds the store."""


def locking_available() -> bool:
    """True when this platform and configuration can actually enforce the protocol."""
    return fcntl is not None and os.environ.get("MEMORY_LOCK", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def wait_timeout() -> float:
    """Shared-lock patience, overridable with ``MEMORY_LOCK_TIMEOUT`` (seconds)."""
    raw = os.environ.get("MEMORY_LOCK_TIMEOUT")
    if raw is None:
        return DEFAULT_WAIT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_WAIT


def lock_path_for(db_path: str | Path) -> Path:
    """``context/memory-graph.db`` → ``context/memory-graph.db.lock`` (a sibling)."""
    return Path(str(db_path) + LOCK_SUFFIX)


class _PathLock:
    """The process's single view of one lock file: one fd, shared by every holder here.

    ``flock`` is per *file description*, not per process, so two fds on one file in one
    process conflict with each other exactly as two processes would. That would be a
    self-deadlock waiting to happen (the GUI opening a second store, a test opening two),
    so the process funnels all holders of a path through one fd — and then has to do the
    compatibility check itself, in ``_counts``, because the kernel can no longer see the
    difference between "me" and "the other holder in me".
    """

    __slots__ = ("path", "fd", "shared", "exclusive")

    def __init__(self, path: str) -> None:
        self.path = path
        self.fd: int | None = None
        self.shared = 0
        self.exclusive = 0

    def held(self) -> int:
        return self.shared + self.exclusive


class _Held:
    """A taken lock. Opaque to callers; pass it back to ``release``."""

    __slots__ = ("key", "exclusive")

    def __init__(self, key: str, exclusive: bool) -> None:
        self.key = key
        self.exclusive = exclusive


_registry: dict[str, _PathLock] = {}
_guard = threading.Lock()


def _flock(fd: int, exclusive: bool) -> bool | None:
    """Try once, without blocking. True taken, False held elsewhere, None unsupported."""
    mode = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    try:
        fcntl.flock(fd, mode)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            return False
        if exc.errno in _UNSUPPORTED:
            return None
        raise
    return True


def acquire(
    db_path: str | Path, *, exclusive: bool = False, timeout: float | None = None
) -> _Held | None:
    """Take the store lock, or raise ``StoreBusy``. ``None`` means nothing to lock.

    ``None`` comes back for in-memory stores and when locking is unavailable or switched
    off — a value ``release`` accepts, so no call site needs a branch for it.
    """
    if str(db_path) in _IN_MEMORY or not locking_available():
        return None
    lock_file = lock_path_for(db_path)
    key = str(lock_file.absolute())
    limit = (REPLACE_WAIT if exclusive else wait_timeout()) if timeout is None else timeout
    deadline = time.monotonic() + limit

    while True:
        with _guard:
            state = _registry.setdefault(key, _PathLock(key))
            # An exclusive request loses to any holder, including one inside this
            # process; a shared request only loses to an exclusive one.
            in_process_conflict = state.exclusive > 0 or (exclusive and state.shared > 0)
            if not in_process_conflict:
                if state.held() and not exclusive:
                    state.shared += 1  # ride the shared flock this process already holds
                    return _Held(key, False)
                if state.fd is None:
                    try:
                        lock_file.parent.mkdir(parents=True, exist_ok=True)
                        state.fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
                    except OSError:
                        _registry.pop(key, None)
                        return None  # cannot even make a lock file: degrade, never crash
                taken = _flock(state.fd, exclusive)
                if taken is None:
                    os.close(state.fd)
                    _registry.pop(key, None)
                    return None  # filesystem does not support flock
                if taken:
                    if exclusive:
                        state.exclusive += 1
                    else:
                        state.shared += 1
                    return _Held(key, exclusive)
                if state.held() == 0:
                    os.close(state.fd)  # do not hoard an fd we are not holding a lock on
                    state.fd = None
                    _registry.pop(key, None)
        if time.monotonic() >= deadline:
            what = "rebuild" if exclusive else "open"
            raise StoreBusy(
                f"could not {what} {db_path}: another process holds the store "
                f"(waited {limit:g}s)"
            )
        time.sleep(_POLL)


def release(held: _Held | None) -> None:
    """Give back a lock taken by ``acquire``. Accepts ``None``, so callers stay branchless."""
    if held is None:
        return
    with _guard:
        state = _registry.get(held.key)
        if state is None:
            return
        if held.exclusive:
            state.exclusive = max(0, state.exclusive - 1)
        else:
            state.shared = max(0, state.shared - 1)
        if state.held() == 0:
            if state.fd is not None:
                try:
                    fcntl.flock(state.fd, fcntl.LOCK_UN)
                finally:
                    os.close(state.fd)
            _registry.pop(held.key, None)


@contextmanager
def store_lock(db_path: str | Path, *, exclusive: bool = False, timeout: float | None = None):
    """Scoped ``acquire`` / ``release``."""
    held = acquire(db_path, exclusive=exclusive, timeout=timeout)
    try:
        yield held
    finally:
        release(held)
