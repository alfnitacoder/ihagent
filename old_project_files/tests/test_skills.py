"""Skills loader for write-code recipes."""

from pathlib import Path

from hfagent.skills import (
    format_skills_for_prompt,
    load_skills,
    skills_index,
)


def test_builtin_write_code_skills_load():
    skills = load_skills()
    ids = {s.id for s in skills}
    assert "write-code" in ids
    assert "edit-code" in ids
    assert "verify-code" in ids
    assert "complete-webapp" in ids
    write = next(s for s in skills if s.id == "write-code")
    assert "write_file" in write.body
    assert write.source == "builtin"


def test_format_skills_mentions_tools_not_announce():
    block = format_skills_for_prompt()
    assert "Coding skills" in block
    assert "write_file" in block
    assert "never only announce" in block.lower() or "never" in block.lower()


def test_project_skill_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".hfagent" / "skills" / "write-code"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Write code (project)\n"
        "description: Project override\n"
        "---\n\n"
        "Project-specific write steps.\n"
    )
    skills = load_skills(project_root=tmp_path)
    write = next(s for s in skills if s.id == "write-code")
    assert write.source == "project"
    assert "Project-specific" in write.body


def test_skills_index_lists_ids():
    index = skills_index()
    assert "write-code" in index
    assert "[builtin]" in index
