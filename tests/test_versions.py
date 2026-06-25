"""Version-consistency guard (scripts/check_versions.py): the four version declarations
— PyPI (pyproject.toml + src/haid/__init__.py) and plugin (.claude-plugin/{plugin,
marketplace}.json) — must agree, or `/plugin update` and `pip` report different versions.
This is the offline contributor tripwire; publish.yml additionally pins them to the tag.
"""

import importlib.util
from pathlib import Path

_CHECKER = Path(__file__).resolve().parent.parent / "scripts" / "check_versions.py"
_spec = importlib.util.spec_from_file_location("check_versions", _CHECKER)
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)


def test_all_version_declarations_agree():
    problems = cv.check()
    assert not problems, "\n".join(problems)


def test_collects_all_four_sources():
    """If a version-bearing file is added/removed, update the guard deliberately."""
    keys = set(cv.collect())
    assert "pyproject.toml" in keys
    assert "src/haid/__init__.py" in keys
    assert any(k.startswith(".claude-plugin/plugin.json") for k in keys)
    assert any(k.startswith(".claude-plugin/marketplace.json") for k in keys)
