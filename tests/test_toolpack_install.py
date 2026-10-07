from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from awsuite import install, tools
from awsuite.toolpack import register

REPO_AWDK = Path(__file__).resolve().parents[4] / "awdk"


class AdkShapedRegistry:
    """Matches awdk ToolRegistry.register's signature (the loader passes the agent's)."""

    def __init__(self):
        self.tools = {}

    def register(self, fn, name=None, description=None, required_clearance=0,
                 action_class="", intent_categories=None, expose_to_a2a=False):
        self.tools[name or fn.__name__] = {"fn": fn, "description": description,
                                           "action_class": action_class}


class BareRegistry:
    def __init__(self):
        self.fns = []

    def register(self, fn):
        self.fns.append(fn)


def test_register_matches_table():
    reg = AdkShapedRegistry()
    assert register(reg) == len(tools.TOOLS)
    assert list(reg.tools) == [t.name for t in tools.TOOLS]
    for t in tools.TOOLS:
        entry = reg.tools[t.name]
        assert entry["action_class"] == ("write" if t.writes else "")
        assert entry["description"] == t.description


def test_register_falls_back_to_bare_register():
    reg = BareRegistry()
    assert register(reg) == len(tools.TOOLS)
    assert [f.__name__ for f in reg.fns] == [t.name for t in tools.TOOLS]


def test_toolpack_manifest_declares_tools_and_skills():
    text = (install.TOOLPACK_DIR / ".toolpack.yaml").read_text("utf-8")
    assert re.search(r'^id: awsuite$', text, re.M)
    assert '- "suite_*"' in text and "- awsuite.toolpack" in text
    assert sorted(re.findall(r"^\s*-\s*(suite-[a-z-]+)$", text, re.M)) == install.skill_names()


def test_pack_install(tmp_path):
    out = install.install_pack(tmp_path / "packs")
    root = tmp_path / "packs" / "awsuite"
    assert out["dir"] == str(root)
    assert (root / ".toolpack.yaml").is_file() and (root / "__init__.py").is_file()
    for name in install.skill_names():
        assert (root / "skills" / name / "SKILL.md").is_file()
    # the copied __init__ is file-loadable the way the awdk loader does it
    spec = importlib.util.spec_from_file_location("_toolpack_awsuite_t", root / "__init__.py",
                                                  submodule_search_locations=[str(root)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_toolpack_awsuite_t"] = mod
    try:
        spec.loader.exec_module(mod)
        assert mod.register(AdkShapedRegistry()) == len(tools.TOOLS)
    finally:
        sys.modules.pop("_toolpack_awsuite_t", None)


def test_skills_install_and_frontmatter(tmp_path):
    out = install.install_skills(tmp_path / "skills")
    assert len(out["written"]) == 4
    for name in install.skill_names():
        text = (tmp_path / "skills" / name / "SKILL.md").read_text("utf-8")
        fm = text.split("---")[1]
        assert re.search(rf"^name: {re.escape(name)}$", fm, re.M)
        assert re.search(r"^description: .{40,}", fm, re.M)


_HAVE_ADK = (REPO_AWDK / "adk" / "tool_pack_loader.py").is_file() \
    and importlib.util.find_spec("yaml") is not None


@pytest.mark.skipif(not _HAVE_ADK, reason="awdk source tree + PyYAML not available")
def test_real_awdk_loader_discovers_and_registers(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_AWDK))
    install.install_pack(tmp_path)
    monkeypatch.setenv("AITHER_TOOLPACK_DIRS", str(tmp_path))
    from adk.tool_pack_loader import ToolPackLoader
    from adk.tools import ToolRegistry

    loader = ToolPackLoader(enforce_entitlements=False)
    found = loader.discover()
    assert "awsuite" in found and sorted(found["awsuite"].skills) == install.skill_names()
    assert all(found["awsuite"].tool_matches(t.name) for t in tools.TOOLS)

    class Agent:
        _tools = ToolRegistry()

    assert loader.register_on_adk_agent(found["awsuite"], Agent()) == len(tools.TOOLS)
    for t in tools.TOOLS:
        assert Agent._tools._tools[t.name].parameters == t.input_schema
