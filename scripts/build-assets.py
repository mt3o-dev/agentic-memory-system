#!/usr/bin/env python3
"""Package the install-only-what-you-need release assets (stdlib only).

The Python distribution (wheel + sdist) is built by ``uv build`` — this script
adds the two tarballs that don't come from packaging metadata, into ``dist/``:

  * ``memory-gui-<ver>.tar.gz``    — the built Svelte GUI (``gui/dist``), a static
                                     bundle to serve behind the GUI API.
  * ``memory-skills-<ver>.tar.gz`` — the ``memory-*`` agentic-memory skills plus an
                                     ``install.sh`` that drops them into a skills dir.

    uv build                                   # wheel + sdist
    python scripts/build-assets.py all         # gui + skills tarballs
    python scripts/build-assets.py gui
    python scripts/build-assets.py skills --version 1.2.3
"""

from __future__ import annotations

import argparse
import tarfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUI_DIST = REPO / "gui" / "dist"
SKILLS = REPO / ".claude" / "skills"
INSTALLER = REPO / "scripts" / "install-skills.sh"
DIST = REPO / "dist"


def read_version() -> str:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data.get("project", {}).get("version", "0.0.0")


def _skip_junk(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if "__pycache__" in ti.name or ti.name.endswith(".pyc") else ti


def build_gui(version: str) -> Path:
    """Tar the tracked, prebuilt GUI (gui/dist) as a servable static bundle."""
    if not (GUI_DIST / "index.html").is_file():
        raise SystemExit(f"error: no built GUI at {GUI_DIST} (index.html missing)")
    DIST.mkdir(exist_ok=True)
    top = f"memory-gui-{version}"
    target = DIST / f"{top}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        tar.add(GUI_DIST, arcname=top, filter=_skip_junk)
    print(f"  {top}.tar.gz  {target.stat().st_size / 1024:7.1f} KB  ({target})")
    return target


def build_skills(version: str) -> Path:
    """Tar the memory-* skills with an install.sh under a versioned top directory."""
    DIST.mkdir(exist_ok=True)
    top = f"memory-skills-{version}"
    target = DIST / f"{top}.tar.gz"
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir() and p.name.startswith("memory-"))
    if not skills:
        raise SystemExit(f"error: no memory-* skills under {SKILLS}")
    with tarfile.open(target, "w:gz") as tar:
        tar.add(INSTALLER, arcname=f"{top}/install.sh")
        for skill in skills:
            tar.add(skill, arcname=f"{top}/skills/{skill.name}", filter=_skip_junk)
    print(f"  {top}.tar.gz  {target.stat().st_size / 1024:7.1f} KB  "
          f"({len(skills)} skills, {target})")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", nargs="?", default="all", choices=["all", "gui", "skills"])
    parser.add_argument("--version", help="override the version (default: pyproject version)")
    args = parser.parse_args(argv)

    version = args.version or read_version()
    print(f"packaging agentic-memory assets (version {version}) → {DIST}/")
    if args.what in ("all", "gui"):
        build_gui(version)
    if args.what in ("all", "skills"):
        build_skills(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
