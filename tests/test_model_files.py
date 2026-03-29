"""Tests for 3D model file metadata extraction."""

import json
import struct
import zipfile
from pathlib import Path

import pytest

from keep.providers.model_files import extract_3d_metadata


# ---------------------------------------------------------------------------
# Helpers to generate minimal valid files
# ---------------------------------------------------------------------------

def _write_binary_stl(path: Path, triangles: list[tuple]) -> None:
    """Write a minimal binary STL.

    triangles: list of ((nx,ny,nz), (v1x,v1y,v1z), (v2x,v2y,v2z), (v3x,v3y,v3z))
    """
    header = b"Binary STL test file" + b"\x00" * 60  # 80 bytes
    with open(path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(triangles)))
        for normal, v1, v2, v3 in triangles:
            f.write(struct.pack("<3f", *normal))
            f.write(struct.pack("<3f", *v1))
            f.write(struct.pack("<3f", *v2))
            f.write(struct.pack("<3f", *v3))
            f.write(struct.pack("<H", 0))  # attribute byte count


def _write_ascii_stl(path: Path, name: str, triangles: list[tuple]) -> None:
    lines = [f"solid {name}"]
    for normal, v1, v2, v3 in triangles:
        lines.append(f"  facet normal {normal[0]} {normal[1]} {normal[2]}")
        lines.append("    outer loop")
        for v in (v1, v2, v3):
            lines.append(f"      vertex {v[0]} {v[1]} {v[2]}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines))


def _write_obj(path: Path, vertices: list[tuple], faces: list[tuple],
               groups: list[str] | None = None,
               materials: list[str] | None = None) -> None:
    lines = ["# test OBJ file"]
    if materials:
        lines.append(f"mtllib test.mtl")
    for v in vertices:
        lines.append(f"v {v[0]} {v[1]} {v[2]}")
    if groups:
        for g in groups:
            lines.append(f"g {g}")
    if materials:
        for m in materials:
            lines.append(f"usemtl {m}")
    for f in faces:
        lines.append("f " + " ".join(str(i) for i in f))
    path.write_text("\n".join(lines))


def _write_ply(path: Path, vertex_count: int, face_count: int,
               fmt: str = "ascii") -> None:
    lines = [
        "ply",
        f"format {fmt} 1.0",
        f"element vertex {vertex_count}",
        "property float x",
        "property float y",
        "property float z",
        f"element face {face_count}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    path.write_text("\n".join(lines))


def _write_gltf(path: Path, meshes: int = 1, materials: int = 1,
                scene_name: str = "Scene") -> None:
    data = {
        "asset": {"version": "2.0", "generator": "test"},
        "scenes": [{"name": scene_name, "nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}] * meshes,
        "materials": [{"name": f"mat{i}"} for i in range(materials)],
        "accessors": [{
            "type": "VEC3",
            "componentType": 5126,
            "count": 4,
            "min": [0.0, 0.0, 0.0],
            "max": [10.0, 20.0, 30.0],
        }],
    }
    path.write_text(json.dumps(data))


def _write_glb(path: Path, gltf_data: dict) -> None:
    json_bytes = json.dumps(gltf_data).encode("utf-8")
    # Pad to 4-byte alignment
    while len(json_bytes) % 4:
        json_bytes += b" "
    chunk_header = struct.pack("<II", len(json_bytes), 0x4E4F534A)
    total_length = 12 + 8 + len(json_bytes)
    header = b"glTF" + struct.pack("<II", 2, total_length)
    with open(path, "wb") as f:
        f.write(header)
        f.write(chunk_header)
        f.write(json_bytes)


def _write_3mf(path: Path, objects: int = 1, verts_per: int = 4,
               tris_per: int = 2) -> None:
    ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    vert_xml = "".join(
        f'<vertex x="{i}" y="{i}" z="{i}" />' for i in range(verts_per)
    )
    tri_xml = "".join(
        f'<triangle v1="0" v2="{i+1}" v3="{min(i+2, verts_per-1)}" />'
        for i in range(tris_per)
    )
    obj_xml = "".join(
        f'<object id="{i+1}" type="model"><mesh>'
        f"<vertices>{vert_xml}</vertices>"
        f"<triangles>{tri_xml}</triangles>"
        f"</mesh></object>"
        for i in range(objects)
    )
    model_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<model xmlns="{ns}"><resources>{obj_xml}</resources></model>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", model_xml)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSTL:
    """Tests for STL file parsing."""
    def test_binary_stl(self, tmp_path):
        p = tmp_path / "cube.stl"
        tris = [
            ((0, 0, 1), (0, 0, 0), (10, 0, 0), (10, 10, 0)),
            ((0, 0, 1), (0, 0, 0), (10, 10, 0), (0, 10, 0)),
            ((0, 0, 1), (0, 0, 5), (10, 0, 5), (10, 10, 5)),
        ]
        _write_binary_stl(p, tris)
        result = extract_3d_metadata(str(p))
        assert "STL (binary)" in result
        assert "3 triangles" in result
        assert "Dimensions:" in result

    def test_ascii_stl(self, tmp_path):
        p = tmp_path / "test.stl"
        tris = [
            ((0, 0, 1), (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 50.0, 0.0)),
            ((0, 0, 1), (0.0, 0.0, 0.0), (100.0, 50.0, 0.0), (0.0, 50.0, 0.0)),
        ]
        _write_ascii_stl(p, "TestSolid", tris)
        result = extract_3d_metadata(str(p))
        assert "STL (ASCII)" in result
        assert "2 triangles" in result
        assert "TestSolid" in result
        assert "100" in result  # dimension

    def test_empty_stl(self, tmp_path):
        p = tmp_path / "empty.stl"
        _write_binary_stl(p, [])
        result = extract_3d_metadata(str(p))
        assert "0 triangles" in result


class TestOBJ:
    """Tests for OBJ file parsing."""
    def test_basic_obj(self, tmp_path):
        p = tmp_path / "cube.obj"
        verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        faces = [(1, 2, 3), (1, 3, 4)]
        _write_obj(p, verts, faces)
        result = extract_3d_metadata(str(p))
        assert "OBJ:" in result
        assert "4 vertices" in result
        assert "2 faces" in result

    def test_obj_with_groups_materials(self, tmp_path):
        p = tmp_path / "scene.obj"
        verts = [(0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 5)]
        faces = [(1, 2, 3), (1, 3, 4)]
        _write_obj(p, verts, faces, groups=["body", "lid"],
                   materials=["wood", "metal"])
        result = extract_3d_metadata(str(p))
        assert "Groups: body, lid" in result
        assert "Materials: wood, metal" in result
        assert "Material lib: test.mtl" in result


class TestPLY:
    """Tests for PLY file parsing."""
    def test_ply_header(self, tmp_path):
        p = tmp_path / "scan.ply"
        _write_ply(p, vertex_count=50000, face_count=100000)
        result = extract_3d_metadata(str(p))
        assert "PLY (ascii)" in result
        assert "50,000 vertex" in result
        assert "100,000 face" in result
        assert "Vertex properties: x, y, z" in result


class TestGLTF:
    """Tests for glTF file parsing."""
    def test_gltf_json(self, tmp_path):
        p = tmp_path / "scene.gltf"
        _write_gltf(p, meshes=2, materials=3, scene_name="MyScene")
        result = extract_3d_metadata(str(p))
        assert "glTF:" in result
        assert "MyScene" in result
        assert "2 meshes" in result
        assert "3 materials" in result
        assert "Dimensions:" in result

    def test_glb(self, tmp_path):
        p = tmp_path / "model.glb"
        gltf_data = {
            "asset": {"version": "2.0"},
            "meshes": [{"primitives": []}],
            "materials": [{"name": "mat0"}],
        }
        _write_glb(p, gltf_data)
        result = extract_3d_metadata(str(p))
        assert "glTF:" in result
        assert "1 mesh" in result


class TestThreeMF:
    """Tests for 3MF file parsing."""
    def test_basic_3mf(self, tmp_path):
        p = tmp_path / "print.3mf"
        _write_3mf(p, objects=2, verts_per=8, tris_per=12)
        result = extract_3d_metadata(str(p))
        assert "3MF:" in result
        assert "2 objects" in result
        assert "16 vertices" in result  # 8 * 2 objects
        assert "24 triangles" in result  # 12 * 2 objects


class TestFallback:
    """Tests for unknown extension fallback."""
    def test_unknown_extension(self, tmp_path):
        p = tmp_path / "model.fbx"
        p.write_bytes(b"\x00" * 100)
        result = extract_3d_metadata(str(p))
        assert result == "[model.fbx]"

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "broken.stl"
        p.write_bytes(b"\x00" * 10)
        result = extract_3d_metadata(str(p))
        # Should not raise, falls back gracefully
        assert "broken.stl" in result
