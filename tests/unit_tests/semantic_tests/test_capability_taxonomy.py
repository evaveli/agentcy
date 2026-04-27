"""Tests for the capability taxonomy hierarchy and expansion."""
from __future__ import annotations

import json
import os
import tempfile

from src.agentcy.semantic.capability_taxonomy import (
    expand_capabilities,
    get_children,
    load_hierarchy,
    _DEFAULT_HIERARCHY,
)


def test_expand_single_leaf():
    """file_read expands to include data_read and io_capability."""
    result = expand_capabilities({"file_read"})
    assert "file_read" in result
    assert "data_read" in result
    assert "io_capability" in result


def test_expand_mid_level():
    """data_read expands to include io_capability but not file_read."""
    result = expand_capabilities({"data_read"})
    assert "data_read" in result
    assert "io_capability" in result
    assert "file_read" not in result


def test_expand_root_unchanged():
    """A root capability (no parent) stays as-is."""
    result = expand_capabilities({"io_capability"})
    assert result == {"io_capability"}


def test_expand_unknown_capability():
    """Unknown capabilities are kept as-is (no ancestors)."""
    result = expand_capabilities({"custom_thing"})
    assert result == {"custom_thing"}


def test_expand_multiple():
    """Multiple capabilities are all expanded."""
    result = expand_capabilities({"file_read", "parse"})
    assert "file_read" in result
    assert "data_read" in result
    assert "io_capability" in result
    assert "parse" in result
    assert "transform" in result
    assert "processing" in result


def test_expand_empty():
    """Empty input returns empty set."""
    result = expand_capabilities(set())
    assert result == set()


def test_expand_case_insensitive():
    """Expansion normalises to lowercase."""
    result = expand_capabilities({"File_Read"})
    assert "file_read" in result
    assert "data_read" in result


def test_expand_with_custom_hierarchy():
    """A custom hierarchy dict overrides defaults."""
    custom = {"red": "colour", "colour": "property"}
    result = expand_capabilities({"red"}, hierarchy=custom)
    assert result == {"red", "colour", "property"}


def test_expand_circular_hierarchy():
    """Circular hierarchy doesn't infinite-loop."""
    circular = {"a": "b", "b": "c", "c": "a"}
    result = expand_capabilities({"a"}, hierarchy=circular)
    assert "a" in result
    assert "b" in result
    assert "c" in result


def test_get_children_basic():
    """data_read's children include file_read and db_read."""
    children = get_children("data_read")
    assert "file_read" in children
    assert "db_read" in children
    assert "data_read" not in children  # not self


def test_get_children_deep():
    """io_capability's children include data_read, file_read, etc."""
    children = get_children("io_capability")
    assert "data_read" in children
    assert "file_read" in children
    assert "db_read" in children
    assert "data_write" in children
    assert "api_call" in children


def test_get_children_leaf():
    """A leaf capability has no children."""
    children = get_children("file_read")
    assert children == set()


def test_get_children_unknown():
    """Unknown capability has no children."""
    children = get_children("nonexistent")
    assert children == set()


def test_load_hierarchy_default():
    """Default hierarchy is returned when no env var is set."""
    hierarchy = load_hierarchy()
    assert hierarchy == _DEFAULT_HIERARCHY


def test_load_hierarchy_from_file(tmp_path, monkeypatch):
    """Custom hierarchy loaded from JSON file."""
    custom = {"x": "y", "y": "z"}
    path = tmp_path / "hierarchy.json"
    path.write_text(json.dumps(custom))
    monkeypatch.setenv("CAPABILITY_HIERARCHY_PATH", str(path))
    hierarchy = load_hierarchy()
    assert hierarchy == custom


def test_load_hierarchy_bad_file_falls_back(tmp_path, monkeypatch):
    """Bad JSON file falls back to defaults."""
    path = tmp_path / "bad.json"
    path.write_text("not json")
    monkeypatch.setenv("CAPABILITY_HIERARCHY_PATH", str(path))
    hierarchy = load_hierarchy()
    assert hierarchy == _DEFAULT_HIERARCHY
