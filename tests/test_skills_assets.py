"""The herdsman-nav skill asset ships, carries valid frontmatter, and cites
only `herdsman nav` subcommands that actually exist in the CLI."""

from pathlib import Path

from typer.testing import CliRunner

from herdsman import cli

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "skills"


def _frontmatter_name(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---\n"), skill_md
    frontmatter = text.split("\n---\n", 1)[0]
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            return line.removeprefix("name:").strip()
    raise AssertionError(f"no name: in {skill_md}")


def test_herdsman_nav_skill_ships_with_valid_frontmatter() -> None:
    skill_md = ASSETS_ROOT / "herdsman-nav" / "SKILL.md"
    assert skill_md.is_file(), sorted(p.parent.name for p in ASSETS_ROOT.glob("*/SKILL.md"))
    assert _frontmatter_name(skill_md) == "herdsman-nav"


def test_herdsman_nav_skill_cites_only_real_nav_subcommands() -> None:
    nav_commands = {
        line.strip().strip("│").split()[0]
        for line in CliRunner().invoke(cli.app, ["nav", "--help"]).stdout.splitlines()
        if line.startswith("│ ") and len(line.strip().strip("│").split()) >= 5
    } - {"--help"}
    text = (ASSETS_ROOT / "herdsman-nav" / "SKILL.md").read_text(encoding="utf-8")
    for snippet in ("nav codemap", "nav tour", "nav flow", "nav symbol", "nav guide"):
        assert snippet in text, snippet
        assert snippet.split()[1] in nav_commands, (snippet, sorted(nav_commands))
