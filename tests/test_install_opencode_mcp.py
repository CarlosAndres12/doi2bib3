"""Tests for the install-opencode-mcp script (tasks 4.1-4.6).

Tests the config-writing logic by importing the script as a module and calling
its functions with temp paths. Venv creation and pip install are mocked or
skipped via --no-install.
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys

import pytest

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "install-opencode-mcp"
)
SCRIPT_PATH = os.path.abspath(SCRIPT_PATH)


def _load_script():
    """Load the install script as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("install_opencode_mcp", SCRIPT_PATH)
    spec = importlib.util.spec_from_loader("install_opencode_mcp", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load_script()


@pytest.fixture
def fake_venv(tmp_path):
    """Create a fake venv with a python binary so verify passes."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    return str(venv)


# --- Workspace config (plain JSON) ------------------------------------------

def test_workspace_config_creates_correct_block(tmp_path, mod, fake_venv, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod.write_workspace_config(fake_venv, force=False)
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    block = cfg["mcp"]["doi2bib3"]
    assert block["type"] == "local"
    assert block["command"] == [os.path.join(fake_venv, "bin", "python"), "-m", "doi2bib3.mcp_server"]
    assert block["enabled"] is True
    assert block["timeout"] == 10000


def test_workspace_existing_block_blocks_by_default(tmp_path, mod, fake_venv, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod.write_workspace_config(fake_venv, force=False)
    with pytest.raises(SystemExit) as exc_info:
        mod.write_workspace_config(fake_venv, force=False)
    assert exc_info.value.code == 1


def test_workspace_force_overwrites_block(tmp_path, mod, fake_venv, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod.write_workspace_config(fake_venv, force=False)
    mod.write_workspace_config(fake_venv, force=True)
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert list(cfg["mcp"].keys()) == ["doi2bib3"]


def test_workspace_idempotent_rerun(tmp_path, mod, fake_venv, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod.write_workspace_config(fake_venv, force=False)
    mod.write_workspace_config(fake_venv, force=True)
    mod.write_workspace_config(fake_venv, force=True)
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert list(cfg["mcp"].keys()) == ["doi2bib3"]


def test_workspace_preserves_other_config(tmp_path, mod, fake_venv, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "lsp": True,
        "mcp": {"other_server": {"type": "local", "command": ["foo"]}},
    }, indent=2))
    mod.write_workspace_config(fake_venv, force=False)
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert "other_server" in cfg["mcp"]
    assert "doi2bib3" in cfg["mcp"]
    assert cfg["lsp"] is True


def test_workspace_custom_venv_path(tmp_path, mod, monkeypatch):
    custom_venv = tmp_path / "custom" / "venv"
    (custom_venv / "bin").mkdir(parents=True)
    (custom_venv / "bin" / "python").touch()
    monkeypatch.chdir(tmp_path)
    mod.write_workspace_config(str(custom_venv), force=False)
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert cfg["mcp"]["doi2bib3"]["command"][0] == str(custom_venv / "bin" / "python")


# --- Global config (JSONC) --------------------------------------------------

def test_global_config_preserves_comments(tmp_path, mod, fake_venv, monkeypatch):
    global_cfg = tmp_path / "opencode.jsonc"
    global_cfg.write_text('''{
  // my comment
  "$schema": "https://opencode.ai/config.json",
  "lsp": true
}
''')
    monkeypatch.setattr(mod, "GLOBAL_CONFIG", str(global_cfg))
    mod.write_global_config(fake_venv, force=False)
    content = global_cfg.read_text()
    assert "// my comment" in content
    # Validate JSON after stripping comments.
    json.loads(mod._strip_jsonc_comments(content))


def test_global_config_valid_json(tmp_path, mod, fake_venv, monkeypatch):
    global_cfg = tmp_path / "opencode.jsonc"
    global_cfg.write_text('{\n  "lsp": true\n}\n')
    monkeypatch.setattr(mod, "GLOBAL_CONFIG", str(global_cfg))
    mod.write_global_config(fake_venv, force=False)
    content = global_cfg.read_text()
    json.loads(mod._strip_jsonc_comments(content))


def test_global_config_existing_block_blocks(tmp_path, mod, fake_venv, monkeypatch):
    global_cfg = tmp_path / "opencode.jsonc"
    global_cfg.write_text('{\n  "mcp": {\n    "doi2bib3": {"type": "local"}\n  }\n}\n')
    monkeypatch.setattr(mod, "GLOBAL_CONFIG", str(global_cfg))
    with pytest.raises(SystemExit) as exc_info:
        mod.write_global_config(fake_venv, force=False)
    assert exc_info.value.code == 1


def test_global_config_force_overwrites(tmp_path, mod, fake_venv, monkeypatch):
    global_cfg = tmp_path / "opencode.jsonc"
    global_cfg.write_text('{\n  "mcp": {\n    "doi2bib3": {"type": "local"}\n  }\n}\n')
    monkeypatch.setattr(mod, "GLOBAL_CONFIG", str(global_cfg))
    mod.write_global_config(fake_venv, force=True)
    content = global_cfg.read_text()
    json.loads(mod._strip_jsonc_comments(content))
    assert "10000" in content  # new timeout value


def test_global_config_creates_new_file(tmp_path, mod, fake_venv, monkeypatch):
    global_cfg = tmp_path / "opencode.jsonc"
    monkeypatch.setattr(mod, "GLOBAL_CONFIG", str(global_cfg))
    mod.write_global_config(fake_venv, force=False)
    content = global_cfg.read_text()
    json.loads(mod._strip_jsonc_comments(content))
    assert "doi2bib3" in content


# --- pip install failure ----------------------------------------------------

def test_pip_failure_exits_nonzero(tmp_path, mod, monkeypatch):
    """Simulate pip install failure: exit non-zero, don't write config."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()

    def fail_pip(venv_path, editable):
        return 1

    monkeypatch.setattr(mod, "run_pip_install", fail_pip)
    monkeypatch.setattr(mod, "create_venv", lambda p: None)
    monkeypatch.setattr(mod, "verify_venv_python", lambda p: None)
    monkeypatch.chdir(tmp_path)

    rc = mod.main(["--venv", str(venv)])
    assert rc == 1
    # Config should NOT exist.
    assert not (tmp_path / "opencode.json").exists()


# --- CLI flags --------------------------------------------------------------

def test_no_install_skips_venv(tmp_path, mod, fake_venv, monkeypatch):
    monkeypatch.chdir(tmp_path)
    called = []
    monkeypatch.setattr(mod, "create_venv", lambda p: called.append("create"))
    monkeypatch.setattr(mod, "run_pip_install", lambda p, e: called.append("pip"))
    rc = mod.main(["--no-install", "--venv", fake_venv])
    assert rc == 0
    assert called == []  # neither create nor pip called
    assert (tmp_path / "opencode.json").exists()