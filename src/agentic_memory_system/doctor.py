"""Diagnose a store, and repair what can be repaired safely.

Issue #5 ended with a hand-written recovery — rebuild from the tracked dump into a temp
file, `os.replace` it in, delete the stale sidecars, re-run the operation before anything
else could race in — and the report said the honest thing about it: *"this worked, but
it's a manual recovery procedure, not a fix."* `locking.py` removed the way that
corruption happened. This is the other half: the procedure, written down as code, so the
next person meets a command instead of a puzzle.

**Diagnosis never writes.** Every check opens the database read-only or looks at the
filesystem, so running `doctor` on a store you suspect cannot make it worse — which is
the whole reason to run it. Repairs happen only under ``--repair``, and each finding
carries the exact one it would apply.

**The repairs are ordered by how much they can cost you.** Deleting an orphaned staging
file is free. Checkpointing a leftover WAL only folds in transactions that were already
committed. Rebuilding the database from the dump *discards whatever the database holds
that the dump does not* — so it is offered only when the database is missing or
unreadable, and otherwise requires ``--force``, because a database that is merely newer
than its dump is un-dumped work rather than damage (`sync.dump_is_stale`).

Rebuilds go through ``sync.restore_from_text``, never a file copy: it holds the exclusive
lock and clears the sidecars as part of the swap, which is the constraint the whole
concurrency design rests on (`docs/10_CONCURRENCY.md`).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import locking, sync
from .serialization import parse_dump

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Finding:
    """One check's verdict, plus the repair that would answer it."""

    level: str
    check: str
    detail: str
    repair: str = ""
    forced: bool = False  # the repair discards data, so --repair alone will not run it


def _sidecars(db: Path) -> list[Path]:
    return [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]


def _staging(db: Path) -> list[Path]:
    dump = sync.dump_path_for(db)
    return sorted(
        list(db.parent.glob(db.name + ".restoring.*"))
        + list(db.parent.glob(dump.name + ".writing.*"))
    )


def _integrity(db: Path) -> tuple[str, str]:
    """(level, detail) from PRAGMA integrity_check + foreign_key_check, read-only."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return FAIL, f"cannot open: {exc}"
    try:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        if rows != ["ok"]:
            return FAIL, "; ".join(rows[:5])
        orphans = conn.execute("PRAGMA foreign_key_check").fetchall()
        if orphans:
            return FAIL, f"{len(orphans)} row(s) reference a missing node"
        nodes = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
        edges = conn.execute("SELECT count(*) FROM edges").fetchone()[0]
        events = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        return OK, f"{nodes} nodes, {edges} edges, {events} events"
    except sqlite3.DatabaseError as exc:
        return FAIL, str(exc)
    finally:
        conn.close()


def _held_elsewhere(db: Path) -> bool:
    """True when another process has the store open (so a leftover WAL is not leftover)."""
    try:
        held = locking.acquire(db, exclusive=True, timeout=0.2)
    except locking.StoreBusy:
        return True
    locking.release(held)
    return False


# Trust folds over a node's FULL event history on every recompute and `compact_events`
# is a deliberate no-op, so the cost of a hot node grows without bound. The threshold is
# a smoke alarm, not a limit: it exists so the growth is noticed while it is still cheap.
_BUSY_JOURNAL = 200


def _journal_findings(db: Path) -> list[Finding]:
    """Report the busiest node's journal — the growth `compact_events` does not bound."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        row = conn.execute(
            "SELECT node_id, count(*) AS n FROM events GROUP BY node_id "
            "ORDER BY n DESC LIMIT 1"
        ).fetchone()
        total = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    if row is None:
        return [Finding(OK, "journal", "no events yet")]
    node_id, busiest = row
    if busiest < _BUSY_JOURNAL:
        return [Finding(OK, "journal", f"{total} events, busiest node has {busiest}")]
    return [Finding(
        WARN, "journal",
        f"{total} events; node {node_id} has {busiest}, and trust folds over all of them "
        f"on every recompute. compact_events() is still a no-op stub — and cannot simply "
        f"collapse events, because a fold that reads individual recent ones "
        f"(LastNWindowFold) and a fold that only needs their sum (SumAndClampFold) do not "
        f"agree on what a compacted event means",
        repair="",
    )]


def diagnose(db_path: str | Path) -> list[Finding]:
    """Every check, in the order a person would want to read them. Writes nothing."""
    db = Path(db_path)
    dump = sync.dump_path_for(db)
    findings: list[Finding] = []

    if not locking.locking_available():
        findings.append(Finding(
            WARN, "lock",
            "flock is unavailable here, so nothing stops a rebuild from replacing the "
            "database under a live connection (docs/10_CONCURRENCY.md §8)",
        ))
    else:
        findings.append(Finding(OK, "lock", f"{locking.lock_path_for(db).name} enforced"))

    # --- the dump, first: it is the source of truth, and what the database is judged
    # against. A verdict on the database that does not know whether the dump is usable
    # cannot say whether a problem is recoverable.
    pairs = None
    if not dump.is_file():
        findings.append(Finding(
            FAIL, "dump",
            f"{dump.name} is missing — it is the tracked source of truth and the only "
            "thing a rebuild can be built from",
        ))
    else:
        try:
            pairs = parse_dump(dump.read_text(encoding="utf-8"))
            findings.append(Finding(OK, "dump", f"parses, {len(pairs)} nodes"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is the same verdict
            findings.append(Finding(FAIL, "dump", f"{dump.name} does not parse: {exc}"))
    recoverable = pairs is not None
    rebuild = f"rebuild from {dump.name}" if recoverable else ""

    # --- the database, judged against it ---
    if not db.exists():
        findings.append(Finding(
            # Not a failure when the dump is good: the database is a gitignored build
            # artifact and its absence is the documented state of a fresh clone, which
            # the next open heals without being asked. Only unrecoverable if there is
            # nothing to build it from.
            WARN if recoverable else FAIL, "database",
            f"{db.name} is not built yet — the next open builds it from {dump.name}"
            if recoverable else f"{db.name} is missing and there is no usable dump",
            repair=rebuild,
        ))
    elif not sync.is_database(db):
        findings.append(Finding(
            FAIL, "database",
            f"{db.name} is not a SQLite database — it holds text, which is what an "
            "unfiltered clone of the pre-2026-08 layout produced",
            repair=rebuild,
        ))
    else:
        level, detail = _integrity(db)
        findings.append(Finding(
            level, "integrity", detail, repair=rebuild if level == FAIL else "",
        ))

    # --- do the two agree? ---
    if recoverable and db.exists() and sync.is_database(db):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            in_db = {r[0] for r in conn.execute("SELECT id FROM nodes")}
            conn.close()
        except sqlite3.DatabaseError:
            in_db = None
        if in_db is not None:
            in_dump = {node.id for node, _, _ in pairs}
            if in_db == in_dump:
                findings.append(Finding(OK, "agreement", "database and dump match"))
            else:
                only_db, only_dump = len(in_db - in_dump), len(in_dump - in_db)
                newer = "database" if sync.dump_is_stale(db) else "dump"
                findings.append(Finding(
                    WARN, "agreement",
                    f"{only_db} node(s) only in the database, {only_dump} only in the "
                    f"dump; the {newer} is newer",
                    repair=(
                        f"refresh {dump.name} from the database (agentic-memory sync dump)"
                        if newer == "database"
                        else f"rebuild the database from {dump.name}"
                    ),
                    forced=newer == "database",
                ))

    if db.exists() and sync.is_database(db):
        findings.extend(_journal_findings(db))

    # --- leftovers ---
    live = _held_elsewhere(db)
    wal = Path(str(db) + "-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        if live:
            findings.append(Finding(
                OK, "sidecars", "a WAL is present and the store is open in another "
                "process, which is exactly what that looks like",
            ))
        else:
            findings.append(Finding(
                WARN, "sidecars",
                f"{wal.name} holds {wal.stat().st_size} bytes with nobody holding the "
                "store — a session that died before closing",
                repair="checkpoint it into the database",
            ))
    elif any(p.exists() for p in _sidecars(db)):
        findings.append(Finding(OK, "sidecars", "present but empty"))

    orphans = _staging(db)
    if orphans:
        findings.append(Finding(
            WARN, "staging",
            f"{len(orphans)} abandoned staging file(s): {', '.join(p.name for p in orphans)}",
            repair="delete them",
        ))

    backup = Path(str(db) + ".bak")
    if backup.is_file():
        findings.append(Finding(
            OK, "backup", f"{backup.name} exists ({backup.stat().st_size} bytes) — the "
            "copy taken before the last rebuild",
        ))
    return findings


def repair(db_path: str | Path, force: bool = False) -> list[str]:
    """Apply every repair the findings offer. Returns what was done.

    Re-diagnoses rather than trusting a caller-supplied list: repairing is exactly the
    moment when a stale diagnosis is dangerous.
    """
    db = Path(db_path)
    dump = sync.dump_path_for(db)
    done: list[str] = []

    for orphan in _staging(db):
        orphan.unlink(missing_ok=True)
        done.append(f"deleted abandoned staging file {orphan.name}")

    findings = {f.check: f for f in diagnose(db)}

    sidecars = findings.get("sidecars")
    if sidecars is not None and sidecars.level == WARN:
        sync._checkpoint(db)
        done.append("checkpointed the leftover WAL into the database")

    needs_rebuild = any(
        findings.get(check) is not None and findings[check].level == FAIL
        for check in ("database", "integrity")
    )
    disagrees = findings.get("agreement")
    if disagrees is not None and disagrees.level == WARN and not disagrees.forced:
        needs_rebuild = True

    if needs_rebuild or (force and dump.is_file()):
        if not dump.is_file():
            done.append(f"CANNOT rebuild: {dump.name} is missing")
        elif findings.get("dump") is not None and findings["dump"].level == FAIL:
            done.append(f"CANNOT rebuild: {dump.name} does not parse")
        else:
            if db.is_file() and sync.is_database(db):
                sync._checkpoint(db)
                import shutil

                shutil.copy2(db, str(db) + ".bak")
                done.append(f"backed the database up to {db.name}.bak")
            count = sync.restore_from_text(dump.read_text(encoding="utf-8"), db)
            done.append(f"rebuilt {db.name} from {dump.name} ({count} nodes)")
    elif disagrees is not None and disagrees.forced and not force:
        done.append(
            "left the database alone: it holds work the dump does not, which is "
            "un-dumped work rather than damage — `agentic-memory sync dump` publishes "
            "it, or --force to discard it"
        )
    return done


def render(findings: list[Finding]) -> str:
    """The report, widest-first so the verdict is readable before the detail."""
    lines = []
    for f in findings:
        lines.append(f"{f.level.upper():<5} {f.check:<10} {f.detail}")
        if f.repair:
            lines.append(f"      {'':<10} repair: {f.repair}" + (" (needs --force)" if f.forced else ""))
    worst = FAIL if any(f.level == FAIL for f in findings) else (
        WARN if any(f.level == WARN for f in findings) else OK
    )
    lines.append("")
    lines.append({
        OK: "healthy",
        WARN: "usable, with things worth doing — rerun with --repair",
        FAIL: "damaged — rerun with --repair to rebuild from the tracked dump",
    }[worst])
    return "\n".join(lines)


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.level == FAIL for f in findings) else 0
