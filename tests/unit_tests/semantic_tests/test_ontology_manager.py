"""Unit tests for ontology version management."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.agentcy.semantic.ontology_manager import (
    OntologyManager,
    _compute_checksum,
    _now_iso,
)


def test_compute_checksum():
    """Checksum should be consistent for same content."""
    content = "@prefix ac: <http://example.org/> ."
    checksum = _compute_checksum(content)
    assert len(checksum) == 32  # MD5 hex length
    assert checksum == _compute_checksum(content)  # Consistent


def test_compute_checksum_different_content():
    """Different content should produce different checksums."""
    content1 = "@prefix ac: <http://example1.org/> ."
    content2 = "@prefix ac: <http://example2.org/> ."
    assert _compute_checksum(content1) != _compute_checksum(content2)


def test_now_iso_format():
    """Should return ISO-formatted timestamp."""
    timestamp = _now_iso()
    assert "T" in timestamp  # ISO format has T separator
    assert "+" in timestamp or "Z" in timestamp  # Has timezone


def test_manager_no_pool():
    """Manager should handle None pool gracefully."""
    manager = OntologyManager(None)
    assert manager.get_ontology_version() is None
    assert manager.get_shapes_version() is None


@pytest.mark.asyncio
async def test_check_and_sync_ontology_file_not_found(monkeypatch):
    """Should return error when ontology file not found."""
    manager = OntologyManager(None)
    monkeypatch.setenv("ONTOLOGY_PATH", "/nonexistent/file.ttl")

    result = await manager.check_and_sync_ontology()
    assert result["synced"] is False
    assert result["reason"] == "file_not_found"


@pytest.mark.asyncio
async def test_check_and_sync_shapes_file_not_found(monkeypatch):
    """Should return error when shapes file not found."""
    manager = OntologyManager(None)
    monkeypatch.setenv("SHACL_SHAPES_PATH", "/nonexistent/file.ttl")

    result = await manager.check_and_sync_shapes()
    assert result["synced"] is False
    assert result["reason"] == "file_not_found"


@pytest.mark.asyncio
async def test_check_and_sync_ontology_no_changes(monkeypatch, tmp_path):
    """Should skip sync when no changes detected."""
    # Create temp ontology file
    ontology_file = tmp_path / "ontology.ttl"
    content = "@prefix ac: <http://example.org/> ."
    ontology_file.write_text(content)

    manager = OntologyManager(None)

    # Mock get_ontology_version to return matching checksum
    existing_checksum = _compute_checksum(content)
    manager.get_ontology_version = lambda: {"checksum": existing_checksum, "version": 1}

    result = await manager.check_and_sync_ontology(str(ontology_file))
    assert result["synced"] is False
    assert result["reason"] == "no_changes"
    assert result["checksum"] == existing_checksum


@pytest.mark.asyncio
async def test_check_and_sync_ontology_changes_detected(monkeypatch, tmp_path):
    """Should sync when changes detected."""
    from src.agentcy.semantic import ontology_manager as om_module

    # Create temp ontology file
    ontology_file = tmp_path / "ontology.ttl"
    new_content = "@prefix ac: <http://example.org/new#> ."
    ontology_file.write_text(new_content)

    manager = OntologyManager(None)

    # Mock stored version with different checksum
    old_checksum = _compute_checksum("old content")
    manager.get_ontology_version = lambda: {"checksum": old_checksum, "version": 1}
    manager._save_version_doc = MagicMock(return_value=True)

    # Mock Fuseki registration - patch in the ontology_manager module where it's imported
    async def mock_register(*args, **kwargs):
        return True

    monkeypatch.setattr(om_module, "register_ontology", mock_register)

    # Also need to enable Fuseki
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    result = await manager.check_and_sync_ontology(str(ontology_file))
    assert result["synced"] is True
    assert result["version"] == 2
    assert result["previous_version"] == 1


@pytest.mark.asyncio
async def test_check_and_sync_shapes_changes_detected(monkeypatch, tmp_path):
    """Should sync shapes when changes detected."""
    from src.agentcy.semantic import ontology_manager as om_module

    # Create temp shapes file
    shapes_file = tmp_path / "shapes.ttl"
    new_content = "@prefix sh: <http://www.w3.org/ns/shacl#> ."
    shapes_file.write_text(new_content)

    manager = OntologyManager(None)

    # Mock stored version with different checksum
    old_checksum = _compute_checksum("old shapes")
    manager.get_shapes_version = lambda: {"checksum": old_checksum, "version": 2}
    manager._save_version_doc = MagicMock(return_value=True)

    # Mock Fuseki registration - patch in the ontology_manager module
    async def mock_register(*args, **kwargs):
        return True

    monkeypatch.setattr(om_module, "register_shapes", mock_register)
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    result = await manager.check_and_sync_shapes(str(shapes_file))
    assert result["synced"] is True
    assert result["version"] == 3


@pytest.mark.asyncio
async def test_sync_all(monkeypatch, tmp_path):
    """Should sync both ontology and shapes."""
    from src.agentcy.semantic import ontology_manager as om_module

    # Create temp files
    ontology_file = tmp_path / "ontology.ttl"
    ontology_file.write_text("@prefix owl: <http://www.w3.org/2002/07/owl#> .")

    shapes_file = tmp_path / "shapes.ttl"
    shapes_file.write_text("@prefix sh: <http://www.w3.org/ns/shacl#> .")

    monkeypatch.setenv("ONTOLOGY_PATH", str(ontology_file))
    monkeypatch.setenv("SHACL_SHAPES_PATH", str(shapes_file))
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    manager = OntologyManager(None)

    # Mock no existing versions (first sync)
    manager.get_ontology_version = lambda: None
    manager.get_shapes_version = lambda: None
    manager._save_version_doc = MagicMock(return_value=True)

    # Mock Fuseki registration - patch in the ontology_manager module
    async def mock_register(*args, **kwargs):
        return True

    monkeypatch.setattr(om_module, "register_ontology", mock_register)
    monkeypatch.setattr(om_module, "register_shapes", mock_register)

    result = await manager.sync_all()
    assert "ontology" in result
    assert "shapes" in result
    assert result["ontology"]["synced"] is True
    assert result["shapes"]["synced"] is True


@pytest.mark.asyncio
async def test_sync_all_force(monkeypatch, tmp_path):
    """Force sync should upload even if no changes."""
    from src.agentcy.semantic import ontology_manager as om_module

    # Create temp files
    ontology_file = tmp_path / "ontology.ttl"
    content = "@prefix owl: <http://www.w3.org/2002/07/owl#> ."
    ontology_file.write_text(content)

    shapes_file = tmp_path / "shapes.ttl"
    shapes_content = "@prefix sh: <http://www.w3.org/ns/shacl#> ."
    shapes_file.write_text(shapes_content)

    monkeypatch.setenv("ONTOLOGY_PATH", str(ontology_file))
    monkeypatch.setenv("SHACL_SHAPES_PATH", str(shapes_file))
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    manager = OntologyManager(None)

    # Mock existing versions with same checksum
    manager.get_ontology_version = lambda: {
        "checksum": _compute_checksum(content),
        "version": 5,
    }
    manager.get_shapes_version = lambda: {
        "checksum": _compute_checksum(shapes_content),
        "version": 3,
    }
    manager._save_version_doc = MagicMock(return_value=True)

    async def mock_register(*args, **kwargs):
        return True

    monkeypatch.setattr(om_module, "register_ontology", mock_register)
    monkeypatch.setattr(om_module, "register_shapes", mock_register)

    # Without force, should not sync
    result_no_force = await manager.sync_all(force=False)
    assert result_no_force["ontology"]["synced"] is False
    assert result_no_force["ontology"]["reason"] == "no_changes"

    # With force, should sync
    result_force = await manager.sync_all(force=True)
    assert result_force["ontology"]["synced"] is True
    assert result_force["shapes"]["synced"] is True
