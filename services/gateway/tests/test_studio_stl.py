"""STL: read it, measure it, edit it, and never lie about the result.

The tetrahedron built here is the smallest closed solid there is: four facets,
volume 1/6, every edge shared by exactly two triangles. That makes every claim --
"this is watertight", "this is inside out", "the scale doubled the volume eight
times" -- a checkable number rather than an impression.
"""
from __future__ import annotations

import struct

import pytest

from services.gateway import workspace_api as ws
from services.gateway.studio import stl

A = (0.0, 0.0, 0.0)
B = (1.0, 0.0, 0.0)
C = (0.0, 1.0, 0.0)
D = (0.0, 0.0, 1.0)

#: Outward-wound facets of the unit tetrahedron.
TETRA = [(A, C, B), (A, B, D), (B, C, D), (C, A, D)]


def tetra() -> stl.Mesh:
    return stl.Mesh(list(TETRA), "tetra")


def binary_of(triangles, *, header: bytes = b"solid exported-by-some-cad") -> bytes:
    out = bytearray(header.ljust(80, b"\x00"))
    out += struct.pack("<I", len(triangles))
    for a, b, c in triangles:
        out += struct.pack("<12fH", 0.0, 0.0, 0.0, *a, *b, *c, 0)
    return bytes(out)


# --- reading -----------------------------------------------------------------

def test_a_binary_file_is_read_even_when_its_header_says_solid():
    """Plenty of CAD tools write a binary STL whose header starts with "solid"."""
    data = binary_of(TETRA, header=b"solid thing-from-somewhere")

    assert stl.looks_binary(data) is True
    mesh = stl.read(data)
    assert len(mesh) == 4


def test_an_ascii_file_and_a_binary_file_describe_the_same_solid():
    ascii_bytes = stl.write_ascii(tetra()).encode("utf-8")
    binary_bytes = stl.write_binary(tetra())

    from_ascii = stl.read(ascii_bytes)
    from_binary = stl.read(binary_bytes)

    assert stl.looks_binary(ascii_bytes) is False
    assert stl.looks_binary(binary_bytes) is True
    assert len(from_ascii) == len(from_binary) == 4
    assert from_ascii.size() == pytest.approx(from_binary.size())


def test_the_volume_of_the_unit_tetrahedron_is_one_sixth():
    assert tetra().volume() == pytest.approx(1 / 6)


def test_a_closed_solid_has_no_open_edges_and_four_vertices():
    mesh = tetra()
    assert mesh.open_edges() == 0
    assert mesh.vertices() == 4


def test_a_missing_facet_is_reported_as_an_open_surface():
    open_mesh = stl.Mesh(TETRA[1:])

    assert open_mesh.open_edges() == 3
    described = stl.summary(open_mesh)
    assert described["watertight"] is False
    assert any("open" in warning for warning in described["warnings"])


def test_a_mesh_wound_inside_out_is_called_out():
    inverted = stl.Mesh([(a, c, b) for a, b, c in TETRA])

    assert inverted.volume() == pytest.approx(-1 / 6)
    assert any("inside out" in warning for warning in stl.summary(inverted)["warnings"])


def test_a_zero_area_facet_is_counted():
    squashed = stl.Mesh([(A, B, C), (A, B, (0.5, 0.0, 0.0))])  # second facet is a line

    assert squashed.degenerate() == 1


@pytest.mark.parametrize(
    "data, message",
    [
        (b"", "empty"),
        (b"not an stl at all, just some words", "no facets"),
        (binary_of(TETRA, header=b"binary-export")[:120], "truncated"),
    ],
)
def test_an_unreadable_file_is_an_error_naming_why(data, message):
    with pytest.raises(stl.StlError) as caught:
        stl.read(data)
    assert message in str(caught.value)


def test_an_ascii_file_with_a_partial_facet_is_refused():
    broken = "solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nendloop\nendfacet\n"

    with pytest.raises(stl.StlError, match="whole number of triangles"):
        stl.read(broken.encode())


def test_an_enormous_mesh_is_refused_before_it_is_parsed():
    data = bytearray(b"\x00" * 80)
    data += struct.pack("<I", stl.MAX_TRIANGLES + 1)

    with pytest.raises(stl.StlError, match="decimate"):
        stl.read(bytes(data) + b"\x00" * 50 * (stl.MAX_TRIANGLES + 1))


# --- editing -----------------------------------------------------------------

def test_scaling_doubles_the_size_and_the_volume_eight_times():
    doubled = tetra().scaled((2.0, 2.0, 2.0))

    assert doubled.size() == pytest.approx((2.0, 2.0, 2.0))
    assert doubled.volume() == pytest.approx(8 / 6)


def test_fitting_a_model_to_a_length_measures_it_after_the_fit():
    fitted = tetra().fitted(30.0)

    assert max(fitted.size()) == pytest.approx(30.0)


def test_rotating_a_quarter_turn_swaps_two_axes_and_keeps_the_solid_whole():
    turned = tetra().rotated((0.0, 0.0, 90.0))
    size = turned.size()

    assert size[0] == pytest.approx(1.0)
    assert size[1] == pytest.approx(1.0)
    assert turned.volume() == pytest.approx(1 / 6)  # a rotation never changes volume


def test_centering_puts_the_bounding_box_on_the_origin():
    shifted = tetra().translated((10.0, -3.0, 5.0)).centered()

    assert shifted.center() == pytest.approx((0.0, 0.0, 0.0))


def test_a_negative_scale_is_refused_instead_of_mirroring_a_solid_inside_out():
    with pytest.raises(stl.StlError, match="inside out"):
        tetra().scaled((-1.0, 1.0, 1.0))


@pytest.fixture
def workspace(monkeypatch):
    """A workspace holding one STL; records whatever is written back."""
    state = {"files": {"parts/bracket.stl": binary_of(TETRA)}, "written": []}

    async def fake_read_bytes(workspace_id, path, **kwargs):
        if path not in state["files"]:
            raise ws.WorkspaceError(f"not found: {path}")
        return state["files"][path]

    async def fake_write_bytes(workspace_id, path, data, **kwargs):
        state["files"][path] = data
        state["written"].append({"path": path, "data": data})
        return {"status": "SUCCESS"}

    monkeypatch.setattr(stl.ws, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(stl.ws, "write_bytes", fake_write_bytes)
    return state


async def test_inspecting_a_workspace_model_reports_it_readable(workspace):
    described = await stl.inspect("ws1", "parts/bracket.stl")

    assert described["triangles"] == 4
    assert described["watertight"] is True
    assert described["format"] == "binary"
    assert described["volume"] == pytest.approx(1 / 6, abs=1e-4)


async def test_an_edit_writes_a_new_file_and_reports_both_measurements(workspace):
    result = await stl.transform("ws1", "parts/bracket.stl", fit=20.0, center=True)

    assert result["path"] == "parts/bracket_edited.stl"
    assert workspace["written"], "the edited mesh has to land in the workspace"
    assert workspace["files"]["parts/bracket.stl"] == binary_of(TETRA), "the original must survive"
    assert result["before"]["longest"] == pytest.approx(1.0)
    assert result["after"]["longest"] == pytest.approx(20.0)
    assert result["applied"] == {"fit_longest": 20.0, "centered": True}

    # and what was written parses back to the same measurements
    written = stl.read(workspace["files"]["parts/bracket_edited.stl"])
    assert max(written.size()) == pytest.approx(20.0)


async def test_an_edit_can_leave_the_original_format_as_ascii(workspace):
    result = await stl.transform("ws1", "parts/bracket.stl", scale=2.0, format="ascii")

    assert result["format"] == "ascii"
    assert workspace["files"]["parts/bracket_edited.stl"].startswith(b"solid ")


async def test_an_edit_with_nothing_to_do_is_refused(workspace):
    with pytest.raises(stl.StlError, match="nothing to do"):
        await stl.transform("ws1", "parts/bracket.stl")


async def test_an_unknown_format_is_refused(workspace):
    with pytest.raises(stl.StlError, match="binary"):
        await stl.transform("ws1", "parts/bracket.stl", scale=2.0, format="obj")


async def test_a_missing_model_names_the_file_it_could_not_read(workspace):
    with pytest.raises(ws.WorkspaceError, match=r"nope\.stl"):
        await stl.inspect("ws1", "nope.stl")


# --- the viewer --------------------------------------------------------------

def test_the_preview_merges_shared_vertices_and_keeps_the_faces():
    view = stl.to_indexed(tetra())

    assert view["vertices"] == 4  # four corners, not twelve
    assert len(view["faces"]) == 12  # three indices per facet
    assert len(view["positions"]) == 12  # three floats per vertex
    assert view["truncated"] is False


def test_a_large_model_previews_partially_rather_than_not_at_all():
    big = stl.Mesh(list(TETRA) * 10)
    view = stl.to_indexed(big, limit=8)

    assert view["triangles"] == 8
    assert view["truncated"] is True

