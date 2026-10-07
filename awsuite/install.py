"""Install the awdk toolpack and the skills into the directories that discover them.

* `awsuite pack install` copies the toolpack (`.toolpack.yaml`, `__init__.py`)
  and the skills to `<dir>/awsuite/`. awdk scans `~/.aitheros/packs` and every
  directory in `AITHER_TOOLPACK_DIRS`, so either location makes the pack
  discoverable by name.
* `awsuite skills install` copies each `skills/<name>/SKILL.md` to
  `<dir>/<name>/SKILL.md` (Claude Code reads `~/.claude/skills`).

Both overwrite only the files they own and report what they wrote.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List

PKG = Path(__file__).resolve().parent
TOOLPACK_DIR = PKG / "toolpack"
SKILLS_DIR = PKG / "skills"
TOOLPACK_FILES = (".toolpack.yaml", "__init__.py")


def default_pack_dir() -> Path:
    return Path.home() / ".aitheros" / "packs"


def default_skills_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def skill_names() -> List[str]:
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md"))


def install_skills(dest: "Path | str | None" = None) -> Dict[str, object]:
    root = Path(dest).expanduser() if dest else default_skills_dir()
    written: List[str] = []
    for name in skill_names():
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SKILLS_DIR / name / "SKILL.md", target / "SKILL.md")
        written.append(str(target / "SKILL.md"))
    return {"dir": str(root), "skills": skill_names(), "written": written}


def install_pack(dest: "Path | str | None" = None) -> Dict[str, object]:
    root = Path(dest).expanduser() if dest else default_pack_dir()
    target = root / "awsuite"
    target.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for fname in TOOLPACK_FILES:
        src = TOOLPACK_DIR / fname
        if not src.is_file():
            raise FileNotFoundError(f"toolpack file missing from the install: {src}")
        shutil.copyfile(src, target / fname)
        written.append(str(target / fname))
    skills = install_skills(target / "skills")
    written.extend(skills["written"])  # type: ignore[arg-type]
    return {"dir": str(target), "written": written, "skills": skills["skills"],
            "discover": "awdk scans ~/.aitheros/packs and AITHER_TOOLPACK_DIRS"}
