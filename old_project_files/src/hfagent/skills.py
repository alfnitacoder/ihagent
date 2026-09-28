"""Load reusable SKILL.md recipes for the coding agent.

Search order (later overrides earlier by skill name):
  1. Bundled skills shipped with hfagent (builtin_skills/)
  2. User skills in ~/.hfagent/skills/<name>/SKILL.md
  3. Project skills in ./.hfagent/skills/<name>/SKILL.md

Frontmatter matches Cursor/Grok skill shape:
  ---
  name: Write code
  description: Use when creating or rewriting source files
  ---
  # Steps
  ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

USER_SKILLS_ROOT = Path("~/.hfagent/skills").expanduser()
PROJECT_SKILLS_ROOT = Path(".hfagent/skills")
MAX_SKILLS_CHARS = 12_000

FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    body: str
    source: str  # builtin | user | project

    def render(self) -> str:
        header = f"### {self.name} (`{self.id}`)"
        if self.description:
            header += f"\nWhen to use: {self.description}"
        return f"{header}\n\n{self.body.strip()}"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text.strip())
    if not match:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, match.group(2).strip()


def _load_skill_file(path: Path, source: str, skill_id: str | None = None) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_frontmatter(text)
    sid = skill_id or path.parent.name
    name = meta.get("name") or sid.replace("-", " ").title()
    description = meta.get("description") or ""
    if not body:
        return None
    return Skill(id=sid, name=name, description=description, body=body, source=source)


def _iter_dir_skills(root: Path, source: str) -> list[Skill]:
    if not root.is_dir():
        return []
    found: list[Skill] = []
    for child in sorted(root.iterdir()):
        skill_md = child / "SKILL.md" if child.is_dir() else None
        if child.is_file() and child.name.endswith(".md"):
            skill = _load_skill_file(child, source, skill_id=child.stem)
            if skill:
                found.append(skill)
        elif skill_md and skill_md.is_file():
            skill = _load_skill_file(skill_md, source, skill_id=child.name)
            if skill:
                found.append(skill)
    return found


def _builtin_skills() -> list[Skill]:
    found: list[Skill] = []
    try:
        root = resources.files("hfagent").joinpath("builtin_skills")
    except Exception:
        return found
    if not root.is_dir():
        # fallback for editable installs / plain path
        fallback = Path(__file__).resolve().parent / "builtin_skills"
        return _iter_dir_skills(fallback, "builtin")
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = _parse_frontmatter(text)
        sid = child.name
        name = meta.get("name") or sid.replace("-", " ").title()
        description = meta.get("description") or ""
        if body:
            found.append(
                Skill(
                    id=sid,
                    name=name,
                    description=description,
                    body=body,
                    source="builtin",
                )
            )
    if not found:
        fallback = Path(__file__).resolve().parent / "builtin_skills"
        return _iter_dir_skills(fallback, "builtin")
    return found


def load_skills(
    *,
    project_root: Path | None = None,
    include_builtin: bool = True,
) -> list[Skill]:
    """Return merged skills; project overrides user overrides builtin."""
    by_id: dict[str, Skill] = {}
    if include_builtin:
        for skill in _builtin_skills():
            by_id[skill.id] = skill
    for skill in _iter_dir_skills(USER_SKILLS_ROOT, "user"):
        by_id[skill.id] = skill
    root = PROJECT_SKILLS_ROOT if project_root is None else project_root / ".hfagent" / "skills"
    for skill in _iter_dir_skills(root, "project"):
        by_id[skill.id] = skill
    return sorted(by_id.values(), key=lambda s: s.id)


def format_skills_for_prompt(skills: list[Skill] | None = None) -> str:
    skills = skills if skills is not None else load_skills()
    if not skills:
        return ""
    priority = {
        "frontend-design": 0,
        "complete-webapp": 1,
        "write-code": 2,
        "edit-code": 3,
        "verify-code": 4,
        "webapp-test-fix": 5,
    }
    ordered = sorted(skills, key=lambda s: (priority.get(s.id, 100), s.id))
    chunks: list[str] = []
    total = 0
    for skill in ordered:
        block = skill.render()
        if len(block) > MAX_SKILLS_CHARS:
            continue
        if total + len(block) > MAX_SKILLS_CHARS:
            break
        chunks.append(block)
        total += len(block)
    if not chunks:
        return ""
    return (
        "Coding skills (follow these recipes when they apply; still call tools "
        "immediately — never only announce the plan):\n\n"
        + "\n\n".join(chunks)
    )


def skills_index(skills: list[Skill] | None = None) -> str:
    skills = skills if skills is not None else load_skills()
    if not skills:
        return "(no skills loaded)"
    lines = []
    for skill in skills:
        desc = f" — {skill.description}" if skill.description else ""
        lines.append(f"{skill.id} [{skill.source}]{desc}")
    return "\n".join(lines)
