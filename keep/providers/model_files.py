"""3D model file metadata extraction — pure stdlib, no dependencies.

Extracts structural metadata (triangle/vertex counts, bounding boxes,
dimensions) from common 3D model formats at ingest time.
"""

from __future__ import annotations

import json
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def extract_3d_metadata(path: str) -> str:
    """Extract structural metadata from a 3D model file.

    Returns human-readable text describing the file's geometry, or
    a bracketed filename fallback on parse failure.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix == ".stl":
            return _parse_stl(p)
        elif suffix == ".obj":
            return _parse_obj(p)
        elif suffix == ".ply":
            return _parse_ply(p)
        elif suffix == ".gltf":
            return _parse_gltf(p)
        elif suffix == ".glb":
            return _parse_glb(p)
        elif suffix == ".3mf":
            return _parse_3mf(p)
    except Exception:
        pass
    return f"[{p.name}]"


def _fmt_num(n: int) -> str:
    """Format integer with thousands separators."""
    return f"{n:,}"


def _fmt_bounds(lo: list[float], hi: list[float]) -> str:
    """Format bounding box and dimensions from min/max arrays."""
    dims = [hi[i] - lo[i] for i in range(3)]
    lines = [
        f"Bounding box: {lo[0]:.4g}–{hi[0]:.4g} × {lo[1]:.4g}–{hi[1]:.4g} × {lo[2]:.4g}–{hi[2]:.4g}",
        f"Dimensions: {dims[0]:.4g} × {dims[1]:.4g} × {dims[2]:.4g}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STL
# ---------------------------------------------------------------------------

def _is_ascii_stl(data: bytes) -> bool:
    """Heuristic: ASCII STL starts with 'solid' and contains 'facet'."""
    if not data[:5].lower().startswith(b"solid"):
        return False
    # Binary STL can also start with 'solid' in the header — check for 'facet'
    # in the first 1KB to confirm it's truly ASCII.
    head = data[:1024]
    return b"facet" in head.lower()


def _parse_stl(path: Path) -> str:
    data = path.read_bytes()
    if _is_ascii_stl(data):
        return _parse_stl_ascii(data.decode("utf-8", errors="replace"), path.name)
    return _parse_stl_binary(data, path.name)


def _parse_stl_binary(data: bytes, name: str) -> str:
    header = data[:80].rstrip(b"\x00").decode("ascii", errors="replace").strip()
    tri_count = struct.unpack_from("<I", data, 80)[0]
    if tri_count == 0:
        return f"STL (binary): {name}\n{header}\n0 triangles"

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    offset = 84
    record_size = 50  # 12 floats (normal + 3 vertices) + 2 byte attribute
    for _ in range(min(tri_count, (len(data) - 84) // record_size)):
        # Skip normal (3 floats = 12 bytes), read 3 vertices (9 floats)
        verts = struct.unpack_from("<9f", data, offset + 12)
        for v in range(3):
            for ax in range(3):
                val = verts[v * 3 + ax]
                if val < lo[ax]:
                    lo[ax] = val
                if val > hi[ax]:
                    hi[ax] = val
        offset += record_size

    lines = [f"STL (binary): {name}"]
    if header:
        lines.append(header)
    lines.append(f"{_fmt_num(tri_count)} triangles")
    lines.append(_fmt_bounds(lo, hi))
    return "\n".join(lines)


def _parse_stl_ascii(text: str, name: str) -> str:
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    tri_count = 0
    solid_name = ""

    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("solid ") and not solid_name:
            solid_name = line.strip()[6:].strip()
        elif stripped.startswith("facet"):
            tri_count += 1
        elif stripped.startswith("vertex"):
            parts = stripped.split()
            if len(parts) >= 4:
                try:
                    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                    for ax, val in enumerate((x, y, z)):
                        if val < lo[ax]:
                            lo[ax] = val
                        if val > hi[ax]:
                            hi[ax] = val
                except ValueError:
                    pass

    lines = [f"STL (ASCII): {name}"]
    if solid_name:
        lines.append(solid_name)
    lines.append(f"{_fmt_num(tri_count)} triangles")
    if tri_count > 0 and lo[0] != float("inf"):
        lines.append(_fmt_bounds(lo, hi))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OBJ
# ---------------------------------------------------------------------------

def _parse_obj(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    verts = 0
    faces = 0
    normals = 0
    texcoords = 0
    groups: list[str] = []
    objects: list[str] = []
    materials: list[str] = []
    mtllib: str = ""

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] == "#":
            continue
        parts = stripped.split(None, 1)
        key = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if key == "v":
            verts += 1
            coords = rest.split()
            if len(coords) >= 3:
                try:
                    x, y, z = float(coords[0]), float(coords[1]), float(coords[2])
                    for ax, val in enumerate((x, y, z)):
                        if val < lo[ax]:
                            lo[ax] = val
                        if val > hi[ax]:
                            hi[ax] = val
                except ValueError:
                    pass
        elif key == "f":
            faces += 1
        elif key == "vn":
            normals += 1
        elif key == "vt":
            texcoords += 1
        elif key == "g" and rest.strip():
            groups.append(rest.strip())
        elif key == "o" and rest.strip():
            objects.append(rest.strip())
        elif key == "usemtl" and rest.strip():
            mat = rest.strip()
            if mat not in materials:
                materials.append(mat)
        elif key == "mtllib":
            mtllib = rest.strip()

    lines = [f"OBJ: {path.name}"]
    if objects:
        lines.append(f"Objects: {', '.join(objects[:10])}")
    lines.append(f"{_fmt_num(verts)} vertices, {_fmt_num(faces)} faces")
    if normals:
        lines.append(f"{_fmt_num(normals)} normals, {_fmt_num(texcoords)} texcoords")
    if verts > 0 and lo[0] != float("inf"):
        lines.append(_fmt_bounds(lo, hi))
    if groups:
        lines.append(f"Groups: {', '.join(groups[:10])}")
    if materials:
        lines.append(f"Materials: {', '.join(materials[:10])}")
    if mtllib:
        lines.append(f"Material lib: {mtllib}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PLY
# ---------------------------------------------------------------------------

def _parse_ply(path: Path) -> str:
    # PLY header is always ASCII, even for binary data formats
    with open(path, "rb") as f:
        header_lines: list[str] = []
        for raw_line in f:
            line = raw_line.decode("ascii", errors="replace").strip()
            header_lines.append(line)
            if line == "end_header":
                break
            if len(header_lines) > 200:
                break

    fmt = "unknown"
    elements: list[tuple[str, int]] = []
    properties: dict[str, list[str]] = {}
    current_element = ""

    for line in header_lines:
        if line.startswith("format "):
            fmt = line.split(None, 2)[1] if len(line.split()) > 1 else "unknown"
        elif line.startswith("element "):
            parts = line.split()
            if len(parts) >= 3:
                ename = parts[1]
                try:
                    ecount = int(parts[2])
                except ValueError:
                    ecount = 0
                elements.append((ename, ecount))
                current_element = ename
                properties[ename] = []
        elif line.startswith("property ") and current_element:
            # "property float x" or "property list uchar int vertex_indices"
            pname = line.split()[-1]
            properties[current_element].append(pname)

    lines = [f"PLY ({fmt}): {path.name}"]
    for ename, ecount in elements:
        lines.append(f"{_fmt_num(ecount)} {ename}")
    vertex_props = properties.get("vertex", [])
    if vertex_props:
        lines.append(f"Vertex properties: {', '.join(vertex_props)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# glTF / GLB
# ---------------------------------------------------------------------------

def _summarize_gltf_json(data: dict, name: str) -> str:
    lines = [f"glTF: {name}"]
    asset = data.get("asset", {})
    if asset.get("generator"):
        lines.append(f"Generator: {asset['generator']}")
    if asset.get("version"):
        lines.append(f"glTF version: {asset['version']}")

    scenes = data.get("scenes", [])
    if scenes and scenes[0].get("name"):
        lines.append(f"Scene: {scenes[0]['name']}")

    meshes = data.get("meshes", [])
    materials = data.get("materials", [])
    animations = data.get("animations", [])
    textures = data.get("textures", [])
    nodes = data.get("nodes", [])

    counts = []
    if meshes:
        counts.append(f"{len(meshes)} mesh{'es' if len(meshes) != 1 else ''}")
    if materials:
        counts.append(f"{len(materials)} material{'s' if len(materials) != 1 else ''}")
    if animations:
        counts.append(f"{len(animations)} animation{'s' if len(animations) != 1 else ''}")
    if textures:
        counts.append(f"{len(textures)} texture{'s' if len(textures) != 1 else ''}")
    if nodes:
        counts.append(f"{len(nodes)} node{'s' if len(nodes) != 1 else ''}")
    if counts:
        lines.append(", ".join(counts))

    # Try to extract bounding box from accessors
    accessors = data.get("accessors", [])
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    found_bounds = False
    for acc in accessors:
        if acc.get("type") == "VEC3" and "min" in acc and "max" in acc:
            amin = acc["min"]
            amax = acc["max"]
            if len(amin) == 3 and len(amax) == 3:
                found_bounds = True
                for ax in range(3):
                    if amin[ax] < lo[ax]:
                        lo[ax] = amin[ax]
                    if amax[ax] > hi[ax]:
                        hi[ax] = amax[ax]
    if found_bounds:
        lines.append(_fmt_bounds(lo, hi))

    return "\n".join(lines)


def _parse_gltf(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _summarize_gltf_json(data, path.name)


def _parse_glb(path: Path) -> str:
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"glTF":
            return f"[{path.name}: not a valid GLB file]"
        _version = struct.unpack("<I", f.read(4))[0]
        _length = struct.unpack("<I", f.read(4))[0]

        # First chunk should be JSON (type 0x4E4F534A = "JSON")
        chunk_length = struct.unpack("<I", f.read(4))[0]
        chunk_type = struct.unpack("<I", f.read(4))[0]
        if chunk_type != 0x4E4F534A:
            return f"[{path.name}: GLB missing JSON chunk]"
        json_data = f.read(chunk_length)

    data = json.loads(json_data.decode("utf-8"))
    return _summarize_gltf_json(data, path.name)


# ---------------------------------------------------------------------------
# 3MF
# ---------------------------------------------------------------------------

_3MF_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def _parse_3mf(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        # The model file is typically at 3D/3dmodel.model
        model_path = None
        for name in zf.namelist():
            if name.lower().endswith(".model"):
                model_path = name
                break
        if model_path is None:
            return f"[{path.name}: no model file found in 3MF archive]"

        xml_data = zf.read(model_path)

    root = ET.fromstring(xml_data)

    # Count objects and mesh geometry
    obj_count = 0
    total_vertices = 0
    total_triangles = 0

    for obj in root.iter(f"{{{_3MF_NS}}}object"):
        obj_count += 1
        for mesh in obj.iter(f"{{{_3MF_NS}}}mesh"):
            verts_elem = mesh.find(f"{{{_3MF_NS}}}vertices")
            if verts_elem is not None:
                total_vertices += len(verts_elem.findall(f"{{{_3MF_NS}}}vertex"))
            tris_elem = mesh.find(f"{{{_3MF_NS}}}triangles")
            if tris_elem is not None:
                total_triangles += len(tris_elem.findall(f"{{{_3MF_NS}}}triangle"))

    lines = [f"3MF: {path.name}"]
    lines.append(f"{obj_count} object{'s' if obj_count != 1 else ''}")
    lines.append(f"{_fmt_num(total_vertices)} vertices, {_fmt_num(total_triangles)} triangles")
    return "\n".join(lines)
