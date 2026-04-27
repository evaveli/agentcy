"""Unit tests for fuseki_init module."""
from __future__ import annotations

import pytest

from src.agentcy.semantic import fuseki_init


@pytest.mark.asyncio
async def test_register_shapes_disabled(monkeypatch):
    """Shapes registration should return False when Fuseki is disabled."""
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await fuseki_init.register_shapes()
    assert result is False


@pytest.mark.asyncio
async def test_register_ontology_disabled(monkeypatch):
    """Ontology registration should return False when Fuseki is disabled."""
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await fuseki_init.register_ontology()
    assert result is False


@pytest.mark.asyncio
async def test_initialize_fuseki_disabled(monkeypatch):
    """Initialize should report disabled status when Fuseki is off."""
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await fuseki_init.initialize_fuseki()
    assert result["enabled"] is False
    assert result["shapes"] is False
    assert result["ontology"] is False


@pytest.mark.asyncio
async def test_register_shapes_file_not_found(monkeypatch):
    """Should return False when shapes file doesn't exist."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")
    monkeypatch.setenv("SHACL_SHAPES_PATH", "/nonexistent/path.ttl")
    result = await fuseki_init.register_shapes()
    assert result is False


@pytest.mark.asyncio
async def test_register_shapes_already_exists(monkeypatch, tmp_path):
    """Should skip upload if graph already exists (idempotent)."""
    # Create a temp shapes file
    shapes_file = tmp_path / "shapes.ttl"
    shapes_file.write_text("@prefix sh: <http://www.w3.org/ns/shacl#> .")

    monkeypatch.setenv("FUSEKI_ENABLE", "1")
    monkeypatch.setenv("SHACL_SHAPES_PATH", str(shapes_file))

    async def mock_check_exists(graph_uri, **kwargs):
        return True  # Graph already exists

    monkeypatch.setattr(fuseki_init, "_check_graph_exists", mock_check_exists)

    result = await fuseki_init.register_shapes(str(shapes_file))
    assert result is True  # Returns True because graph exists


@pytest.mark.asyncio
async def test_register_shapes_upload_success(monkeypatch, tmp_path):
    """Should upload shapes and return True on success."""
    shapes_file = tmp_path / "shapes.ttl"
    shapes_file.write_text("@prefix sh: <http://www.w3.org/ns/shacl#> .")

    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_check_exists(graph_uri, **kwargs):
        return False  # Graph doesn't exist

    async def mock_upload(turtle, graph_uri, **kwargs):
        return True  # Upload succeeds

    monkeypatch.setattr(fuseki_init, "_check_graph_exists", mock_check_exists)
    monkeypatch.setattr(fuseki_init, "_upload_graph", mock_upload)

    result = await fuseki_init.register_shapes(str(shapes_file))
    assert result is True


@pytest.mark.asyncio
async def test_initialize_fuseki_success(monkeypatch, tmp_path):
    """Full initialization should succeed with mocked functions."""
    # Create temp files
    shapes_file = tmp_path / "shapes.ttl"
    shapes_file.write_text("@prefix sh: <http://www.w3.org/ns/shacl#> .")

    ontology_file = tmp_path / "ontology.ttl"
    ontology_file.write_text("@prefix owl: <http://www.w3.org/2002/07/owl#> .")

    monkeypatch.setenv("FUSEKI_ENABLE", "1")
    monkeypatch.setenv("SHACL_SHAPES_PATH", str(shapes_file))
    monkeypatch.setenv("ONTOLOGY_PATH", str(ontology_file))

    async def mock_check_exists(graph_uri, **kwargs):
        return False

    async def mock_upload(turtle, graph_uri, **kwargs):
        return True

    monkeypatch.setattr(fuseki_init, "_check_graph_exists", mock_check_exists)
    monkeypatch.setattr(fuseki_init, "_upload_graph", mock_upload)

    result = await fuseki_init.initialize_fuseki()
    assert result["enabled"] is True
    assert result["shapes"] is True
    assert result["ontology"] is True


def test_resolve_path_absolute(tmp_path):
    """Should resolve absolute paths correctly."""
    test_file = tmp_path / "test.ttl"
    test_file.write_text("test content")

    resolved = fuseki_init._resolve_path(str(test_file))
    assert resolved is not None
    assert resolved.exists()


def test_resolve_path_nonexistent():
    """Should return None for nonexistent paths."""
    resolved = fuseki_init._resolve_path("/definitely/nonexistent/path.ttl")
    assert resolved is None


def test_load_turtle_file_success(tmp_path):
    """Should load turtle content from file."""
    test_file = tmp_path / "test.ttl"
    content = "@prefix ex: <http://example.org/> ."
    test_file.write_text(content)

    loaded = fuseki_init._load_turtle_file(str(test_file))
    assert loaded == content


def test_load_turtle_file_not_found():
    """Should return None for nonexistent file."""
    loaded = fuseki_init._load_turtle_file("/nonexistent/file.ttl")
    assert loaded is None
