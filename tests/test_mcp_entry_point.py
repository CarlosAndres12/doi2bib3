"""Test the missing-extra error path and the console script entry point (task 7.5)."""

import os
import sys
import importlib

import pytest


def test_main_module_imports():
    """The __main__ module imports cleanly when the mcp extra is present."""
    importlib.import_module("doi2bib3.mcp_server.__main__")


def test_main_callable():
    """main() is callable and is a function taking no required args."""
    from doi2bib3.mcp_server.__main__ import main
    assert callable(main)


def test_missing_extra_error_path(monkeypatch):
    """When mcp is not importable, main() prints an actionable error and returns 1."""
    import doi2bib3.mcp_server.__main__ as entry

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("simulated missing mcp", name="mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Ensure the cached 'mcp' import (if any) is removed so the probe re-imports.
    for mod in list(sys.modules):
        if mod == "mcp" or mod.startswith("mcp."):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    rc = entry.main()
    assert rc == 1


def test_console_script_entry_point_registered():
    """The doi2bib3-mcp console script is declared in project metadata."""
    try:
        import importlib.metadata as md
    except ImportError:
        import importlib_metadata as md  # type: ignore
    eps = md.entry_points()
    # Py3.10+ returns a selectable group; older returns dict.
    try:
        names = {ep.name for ep in eps.select(group="console_scripts")}
    except AttributeError:
        names = set(eps.get("console_scripts", []))  # type: ignore
    assert "doi2bib3-mcp" in names


def test_script_shim_exists_and_is_executable():
    """The scripts/doi2bib3-mcp shim exists and is executable."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shim = os.path.join(repo_root, "scripts", "doi2bib3-mcp")
    assert os.path.isfile(shim)
    assert os.access(shim, os.X_OK)