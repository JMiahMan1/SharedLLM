"""Open, measure and edit STL meshes -- without a CAD library.

A workshop that can draw a part but cannot open the file it produced is not a
workshop. STL is the format every printer, slicer and 3D tool agrees on, and it is
small enough to handle in pure Python: a binary STL is an 80-byte header, a
triangle count, and 50 bytes per facet; the ASCII variant spells the same facets
out as text. No numpy, no trimesh, no native build -- it runs wherever the gateway
runs, and it is pure, so every operation is testable without a container.

The rules this module keeps:

* **measure before and after**: ``transform`` returns the mesh's numbers on both
  sides, so a resize is checked, not hoped for;
* **say what is wrong with the mesh** in words a person can act on -- open
  surfaces, inverted winding, degenerate facets -- instead of returning zeros;
* **never invent geometry**: a file that does not parse is an error naming why.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any

from services.gateway import workspace_api as ws

#: Bigger than this and an "edit" is a re-mesh, not a transform: refuse early
#: rather than spend the gateway's memory on a model nobody can preview.
MAX_TRIANGLES = 400_000

#: Files larger than this do not belong in a chat-driven edit.
MAX_BYTES = 64 * 1024 * 1024

Vec = tuple[float, float, float]
Triangle = tuple[Vec, Vec, Vec]

_ASCII_VERTEX = re.compile(
    r"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)"
)


class StlError(ValueError):
    """The file is not an STL we can work with, and here is why."""


@dataclass
class Mesh:
    """A triangle soup: exactly what STL can express."""

    triangles: list[Triangle] = field(default_factory=list)
    name: str = ""

    def __len__(self) -> int:
        return len(self.triangles)

    # -- geometry ---------------------------------------------------------
    def bounds(self) -> tuple[Vec, Vec]:
        """The axis-aligned box around every vertex."""
        if not self.triangles:
            raise StlError("the mesh has no triangles")
        lo = [math.inf, math.inf, math.inf]
        hi = [-math.inf, -math.inf, -math.inf]
        for tri in self.triangles:
            for point in tri:
                for axis in range(3):
                    if point[axis] < lo[axis]:
                        lo[axis] = point[axis]
                    if point[axis] > hi[axis]:
                        hi[axis] = point[axis]
        return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])

    def size(self) -> Vec:
        lo, hi = self.bounds()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    def center(self) -> Vec:
        lo, hi = self.bounds()
        return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)

    def area(self) -> float:
        """Surface area (square units of whatever the file is in)."""
        return sum(_triangle_area(*tri) for tri in self.triangles)

    def volume(self) -> float:
        """Signed volume by the divergence theorem.

        Positive means the outward normals point outwards; negative is the same
        solid inside out, which slicers treat as a hole.
        """
        total = 0.0
        for a, b, c in self.triangles:
            total += _dot(a, _cross(b, c)) / 6.0
        return total

    def open_edges(self) -> int:
        """Edges used by exactly one triangle: zero means the surface is closed."""
        counts: dict[tuple[Vec, Vec], int] = {}
        for tri in self.triangles:
            keys = [_round_point(point) for point in tri]
            for a, b in ((keys[0], keys[1]), (keys[1], keys[2]), (keys[2], keys[0])):
                if a == b:
                    continue
                edge = (a, b) if a < b else (b, a)
                counts[edge] = counts.get(edge, 0) + 1
        return sum(1 for count in counts.values() if count == 1)

    def degenerate(self) -> int:
        """Triangles with no area (repeated or collinear vertices)."""
        return sum(1 for tri in self.triangles if _triangle_area(*tri) <= 1e-12)


    def vertices(self) -> int:
        """How many distinct points the soup holds (3x the facets at worst)."""
        return len({_round_point(point) for tri in self.triangles for point in tri})

    # -- edits (each returns a new mesh; the original is what you compare to)
    def rotated(self, degrees: Vec = (0.0, 0.0, 0.0)) -> Mesh:
        rx, ry, rz = (math.radians(value) for value in degrees)
        if not any((rx, ry, rz)):
            return self
        return Mesh([_rotate_triangle(tri, rx, ry, rz) for tri in self.triangles], self.name)

    def scaled(self, factor: Vec = (1.0, 1.0, 1.0)) -> Mesh:
        fx, fy, fz = factor
        if fx == fy == fz == 1:
            return self
        if fx <= 0 or fy <= 0 or fz <= 0:
            raise StlError(f"a scale of {factor} would turn the model inside out (use rotate)")
        return Mesh([_map_triangle(tri, fx, fy, fz, 0.0, 0.0, 0.0) for tri in self.triangles], self.name)

    def translated(self, offset: Vec = (0.0, 0.0, 0.0)) -> Mesh:
        dx, dy, dz = offset
        if not any((dx, dy, dz)):
            return self
        return Mesh(
            [_map_triangle(tri, 1.0, 1.0, 1.0, dx, dy, dz) for tri in self.triangles], self.name
        )

    def centered(self) -> Mesh:
        """Move the bounding-box centre to the origin (printers expect it there)."""
        cx, cy, cz = self.center()
        return self.translated((-cx, -cy, -cz))

    def fitted(self, longest: float) -> Mesh:
        """Scale so the largest dimension is ``longest`` (mm on most slicers)."""
        if longest <= 0:
            raise StlError("the target size must be greater than zero")
        current = max(self.size())
        if current <= 0:
            raise StlError("the mesh has no size to scale")
        factor = longest / current
        return self.scaled((factor, factor, factor))


def _map_triangle(
    tri: Triangle, fx: float, fy: float, fz: float, dx: float, dy: float, dz: float
) -> Triangle:
    """One facet, scaled about the origin and then moved -- the shared map for both edits."""
    return tuple(  # type: ignore[return-value]
        (x * fx + dx, y * fy + dy, z * fz + dz) for x, y, z in tri
    )


def _round_point(point: Vec) -> Vec:
    # 1e-6 is far below any printable feature and keeps float noise from
    # splitting a shared vertex into two "open" edges.
    return (round(point[0], 6), round(point[1], 6), round(point[2], 6))


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def normal(a: Vec, b: Vec, c: Vec) -> Vec:
    """The unit normal of a facet, or zeros when the facet has no area."""
    raw = _cross(_sub(b, a), _sub(c, a))
    length = _norm(raw)
    if length <= 1e-12:
        return (0.0, 0.0, 0.0)
    return (raw[0] / length, raw[1] / length, raw[2] / length)


def _triangle_area(a: Vec, b: Vec, c: Vec) -> float:
    return _norm(_cross(_sub(b, a), _sub(c, a))) / 2.0


def _rotate_triangle(tri: Triangle, rx: float, ry: float, rz: float) -> Triangle:
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    out: list[Vec] = []
    for x, y, z in tri:
        # X, then Y, then Z -- the order a slicer's "rotate" boxes apply.
        y, z = y * cx - z * sx, y * sx + z * cx
        x, z = x * cy + z * sy, -x * sy + z * cy
        x, y = x * cz - y * sz, x * sz + y * cz
        out.append((x, y, z))
    return (out[0], out[1], out[2])


# --- describing --------------------------------------------------------------

def _unit_hint(longest: float) -> str:
    """STL carries no units; say which reading is most plausible."""
    if longest <= 0:
        return "the model has no size"
    if longest < 1:
        return "likely metres: this is smaller than a millimetre"
    if longest < 300:
        return "likely millimetres"
    if longest < 3000:
        return "likely centimetres, or millimetres for a very large part"
    return "likely microns or an unscaled CAD export: this is metres across"


def summary(
    mesh: Mesh, *, path: str = "", fmt: str = "", size_bytes: int = 0
) -> dict[str, Any]:
    """Everything worth knowing about a mesh, in the words a person would use."""
    lo, hi = mesh.bounds()
    size = mesh.size()
    volume = mesh.volume()
    open_edges = mesh.open_edges()
    degenerate = mesh.degenerate()
    warnings: list[str] = []
    if open_edges:
        warnings.append(
            f"the surface is open ({open_edges:,} edges belong to one triangle only): "
            "a slicer may refuse it or guess the hole"
        )
    if degenerate:
        warnings.append(f"{degenerate:,} triangles have no area (repeated vertices)")
    if volume < 0:
        warnings.append("the mesh is inside out (signed volume is negative): flip its normals")
    if volume == 0 and open_edges == 0:
        warnings.append("the signed volume is zero: the solid may be a flat sheet")
    longest = max(size)
    if longest > 0 and (longest < 0.1 or longest > 5000):
        warnings.append(_unit_hint(longest))
    if len(mesh.triangles) > 200_000:
        warnings.append("a large mesh: edits and previews will be slow")

    center = mesh.center()
    return {
        "path": path,
        "format": fmt,
        "bytes": size_bytes,
        "name": mesh.name,
        "triangles": len(mesh.triangles),
        "vertices": mesh.vertices(),
        "size": {"x": round(size[0], 4), "y": round(size[1], 4), "z": round(size[2], 4)},
        "longest": round(longest, 4),
        "min": {"x": lo[0], "y": lo[1], "z": lo[2]},
        "max": {"x": hi[0], "y": hi[1], "z": hi[2]},
        "center": {"x": round(center[0], 4), "y": round(center[1], 4), "z": round(center[2], 4)},
        "volume": round(volume, 4),
        "area": round(mesh.area(), 4),
        "watertight": open_edges == 0,
        "open_edges": open_edges,
        "degenerate": degenerate,
        "units": _unit_hint(longest),
        "warnings": warnings,
    }


def to_indexed(mesh: Mesh, *, limit: int = 20_000) -> dict[str, Any]:
    """A compact indexed mesh for a browser viewer (positions + faces).

    Shared vertices are merged so a viewer sends ~3 floats per corner instead of
    repeating every triangle, and the triangle list is capped so a big model
    degrades to a partial preview instead of a frozen tab.
    """
    index: dict[Vec, int] = {}
    points: list[float] = []
    faces: list[int] = []
    for tri in mesh.triangles[:limit]:
        for point in tri:
            key = _round_point(point)
            slot = index.get(key)
            if slot is None:
                slot = len(index)
                index[key] = slot
                points.extend((round(point[0], 5), round(point[1], 5), round(point[2], 5)))
            faces.append(slot)
    lo, hi = mesh.bounds()
    return {
        "positions": points,
        "faces": faces,
        "vertices": len(index),
        "triangles": min(len(mesh.triangles), limit),
        "truncated": len(mesh.triangles) > limit,
        "min": {"x": lo[0], "y": lo[1], "z": lo[2]},
        "max": {"x": hi[0], "y": hi[1], "z": hi[2]},
    }


# --- the workspace-facing half ----------------------------------------------

async def load(workspace_id: str, path: str, *, user: str = "default") -> tuple[Mesh, bytes]:
    """Read an STL out of a workspace, refusing what we cannot work with."""
    data = await ws.read_bytes(workspace_id, path, user=user, max_bytes=MAX_BYTES)
    return read(data), data


async def inspect(workspace_id: str, path: str, *, user: str = "default") -> dict[str, Any]:
    """Open a model from a workspace and describe it."""
    data = await ws.read_bytes(workspace_id, path, user=user, max_bytes=MAX_BYTES)
    mesh = read(data)
    return summary(
        mesh,
        path=path,
        fmt="binary" if looks_binary(data) else "ascii",
        size_bytes=len(data),
    )


def _edited_path(path: str) -> str:
    """``parts/bracket.stl`` -> ``parts/bracket_edited.stl``.

    A new file rather than an overwrite: a mistyped scale must not cost the model
    that took an hour to draw.
    """
    stem = path[:-4] if path.lower().endswith(".stl") else path
    return f"{stem}_edited.stl"


async def transform(
    workspace_id: str,
    path: str,
    *,
    out_path: str | None = None,
    scale: float | tuple[float, float, float] = 1.0,
    rotate: tuple[float, float, float] = (0.0, 0.0, 0.0),
    translate: tuple[float, float, float] = (0.0, 0.0, 0.0),
    fit: float | None = None,
    center: bool = False,
    format: str | None = None,
    user: str = "default",
) -> dict[str, Any]:
    """Scale, rotate, centre and move a model, then write it back as a new file.

    Order is the order a person would use: rotate first (so ``fit`` measures the
    model as it will sit), then fit or scale, then centre, then translate into
    place. The answer carries the measurements from both sides of the edit, so a
    caller checks the result instead of trusting it.
    """
    mesh, data = await load(workspace_id, path, user=user)
    before = summary(
        mesh, path=path, fmt="binary" if looks_binary(data) else "ascii", size_bytes=len(data)
    )

    applied: dict[str, Any] = {}
    edited = mesh
    if any(rotate):
        edited = edited.rotated(tuple(float(v) for v in rotate))
        applied["rotate_deg"] = [float(v) for v in rotate]
    if fit is not None:
        edited = edited.fitted(float(fit))
        applied["fit_longest"] = float(fit)
    factors = (float(scale),) * 3 if isinstance(scale, (int, float)) else tuple(
        float(v) for v in scale
    )
    if factors != (1.0, 1.0, 1.0):
        edited = edited.scaled(factors)
        applied["scale"] = list(factors)
    if center:
        edited = edited.centered()
        applied["centered"] = True
    if any(translate):
        edited = edited.translated(tuple(float(v) for v in translate))
        applied["translate"] = [float(v) for v in translate]
    if not applied:
        raise StlError("nothing to do: give a scale, rotate, fit, center or translate")

    target_format = (format or before["format"] or "binary").lower()
    if target_format not in {"binary", "ascii"}:
        raise StlError(f"unknown format {target_format!r}: use 'binary' or 'ascii'")
    payload = (
        write_binary(edited)
        if target_format == "binary"
        else write_ascii(edited).encode("utf-8")
    )

    destination = out_path or _edited_path(path)
    await ws.write_bytes(workspace_id, destination, payload, user=user)
    after = summary(edited, path=destination, fmt=target_format, size_bytes=len(payload))
    return {
        "source": path,
        "path": destination,
        "format": target_format,
        "applied": applied,
        "before": before,
        "after": after,
        "warnings": after["warnings"],
    }


async def preview(
    workspace_id: str, path: str, *, limit: int = 20_000, user: str = "default"
) -> dict[str, Any]:
    """An indexed mesh a browser can draw, straight from a workspace file."""
    mesh, data = await load(workspace_id, path, user=user)
    payload = to_indexed(mesh, limit=limit)
    payload.update({"path": path, "format": "binary" if looks_binary(data) else "ascii"})
    return payload


# --- reading -----------------------------------------------------------------

def looks_binary(data: bytes) -> bool:
    """Binary or ASCII? The triangle count is the only honest test.

    Both formats may start with ``solid``, so a header check alone misfires; the
    declared count landing exactly on the end of the file is the giveaway.
    """
    if len(data) < 84:
        return False
    count = struct.unpack_from("<I", data, 80)[0]
    return 84 + 50 * count <= len(data) and not _ascii_solid_without_count(data)


def _ascii_solid_without_count(data: bytes) -> bool:
    """True when the file claims to be ASCII and its facet count disagrees."""
    if data[:5].lower() != b"solid":
        return False
    count = struct.unpack_from("<I", data, 80)[0]
    return 84 + 50 * count != len(data)


def read(data: bytes, *, max_triangles: int = MAX_TRIANGLES) -> Mesh:
    """Parse an STL (either flavour) into a mesh."""
    if not data:
        raise StlError("the file is empty")
    if len(data) < 25:
        raise StlError(f"not an STL: only {len(data)} bytes")
    if len(data) >= 84 and not _looks_ascii(data):
        # A plausible count that outruns the file is the signature of a truncated
        # binary STL -- say that, instead of "no facets found", which sends the
        # caller looking for the wrong problem.
        declared = struct.unpack_from("<I", data, 80)[0]
        if 0 < declared <= max_triangles and 84 + 50 * declared > len(data):
            raise StlError(
                f"the header declares {declared:,} triangles but the file is only "
                f"{len(data):,} bytes: it looks truncated"
            )
    if looks_binary(data):
        return _read_binary(data, max_triangles=max_triangles)
    return _read_ascii(data, max_triangles=max_triangles)


def _looks_ascii(data: bytes) -> bool:
    """Does the file actually spell out ASCII facets?"""
    return b"facet" in data[:4096] or b"vertex" in data[:4096]


def _read_binary(data: bytes, *, max_triangles: int) -> Mesh:
    name = data[:80].split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    (count,) = struct.unpack_from("<I", data, 80)
    if count == 0:
        raise StlError("the file declares zero triangles")
    if count > max_triangles:
        raise StlError(
            f"the mesh has {count:,} triangles; this tool edits up to {max_triangles:,} "
            "(decimate it in your slicer first)"
        )
    if 84 + 50 * count > len(data):
        raise StlError(
            f"the header declares {count:,} triangles but the file is only {len(data):,} bytes"
        )
    triangles: list[Triangle] = []
    for index in range(count):
        values = struct.unpack_from("<12fH", data, 84 + 50 * index)
        triangles.append(
            (
                (values[3], values[4], values[5]),
                (values[6], values[7], values[8]),
                (values[9], values[10], values[11]),
            )
        )
    return Mesh(triangles, name)


def _read_ascii(data: bytes, *, max_triangles: int) -> Mesh:
    text = data.decode("latin-1", errors="replace")
    points: list[Vec] = []
    for match in _ASCII_VERTEX.finditer(text):
        try:
            points.append((float(match[1]), float(match[2]), float(match[3])))
        except ValueError as exc:  # a truncated last facet, usually
            raise StlError(f"unreadable vertex on line {text.count(chr(10), 0, match.start()) + 1}") from exc
    if not points:
        raise StlError("no facets found: this is neither ASCII nor binary STL")
    if len(points) % 3:
        raise StlError(
            f"{len(points)} vertices is not a whole number of triangles "
            "(the file looks truncated)"
        )
    if len(points) // 3 > max_triangles:
        raise StlError(
            f"the mesh has {len(points) // 3:,} triangles; this tool edits up to "
            f"{max_triangles:,} (decimate it in your slicer first)"
        )
    header = text.lstrip()
    name = ""
    if header[:5].lower() == "solid":
        name = header[5:].split("\n", 1)[0].strip()
    return Mesh([(points[i], points[i + 1], points[i + 2]) for i in range(0, len(points), 3)], name)


# --- writing -----------------------------------------------------------------

def write_binary(mesh: Mesh, *, name: str = "") -> bytes:
    """A binary STL: 80-byte header, facet count, 50 bytes each."""
    label = (name or mesh.name or "sharedllm")[:79].encode("ascii", errors="replace")
    out = bytearray(label.ljust(80, b"\x00"))
    out += struct.pack("<I", len(mesh.triangles))
    for a, b, c in mesh.triangles:
        nx, ny, nz = normal(a, b, c)
        out += struct.pack("<12fH", nx, ny, nz, *a, *b, *c, 0)
    return bytes(out)


def write_ascii(mesh: Mesh, *, name: str = "") -> str:
    """The ASCII STL a person can diff, greppable and hand-editable."""
    label = name or mesh.name or "sharedllm"
    lines = [f"solid {label}"]
    for a, b, c in mesh.triangles:
        nx, ny, nz = normal(a, b, c)
        lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
        lines.append("    outer loop")
        for point in (a, b, c):
            lines.append(f"      vertex {point[0]:.6e} {point[1]:.6e} {point[2]:.6e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {label}")
    return "\n".join(lines) + "\n"

