"""Tests for skill frontmatter."""

from pathlib import Path

import pytest

from helpers import parse_frontmatter


SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
SKILL_FILES = (
    sorted(SKILLS_DIR.rglob("SKILL.md"))
    if SKILLS_DIR.is_dir()
    else []
)


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_has_frontmatter(skill_file):
    """Each SKILL.md has valid YAML frontmatter with name and description."""
    fm, _ = parse_frontmatter(skill_file)
    skill_name = skill_file.parent.name
    assert fm is not None, f"{skill_name}/SKILL.md has no frontmatter"
    assert fm.get("name"), f"{skill_name}/SKILL.md frontmatter missing 'name'"
    assert fm.get("description"), (
        f"{skill_name}/SKILL.md frontmatter missing 'description'"
    )
