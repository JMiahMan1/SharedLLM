"""The advertised APK version must describe the APK actually being served.

These are the regression tests for a real bug: `apk_version_code` was read out
of `build.gradle` on every bundle deploy, so it drifted ahead of the published
artifact and a user who installed the newest APK on offer was told to update
forever.
"""
import io
import struct
import zipfile

import pytest

from services.gateway.apk_manifest import (
    clear_cache,
    parse_manifest,
    read_apk_version,
)


def _string_pool(strings: list[str], utf8: bool = True) -> bytes:
    """Build a real ResStringPool chunk containing `strings`."""
    header_size = 28
    string_count = len(strings)
    flags = 0x100 if utf8 else 0x0000

    offsets = []
    blob = b""
    for s in strings:
        offsets.append(len(blob))
        if utf8:
            # Two varint lengths (chars, then bytes), then the bytes. AOSP also
            # writes a NUL terminator after each string.
            data = s.encode("utf-8")
            assert len(s) < 128 and len(data) < 128, "fixture strings must be short"
            blob += bytes([len(s), len(data)]) + data + b"\x00"
        else:
            blob += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00"

    # stringsStart is the offset from the start of the chunk (including its
    # 8-byte ResChunk_header) to the first string, so it is header_size plus
    # one 4-byte entry per string. `body` excludes that 8-byte header, hence
    # the -8 when comparing against its length.
    strings_start = header_size + string_count * 4
    header = struct.pack("<IIIII", string_count, 0, flags, strings_start, 0)
    offset_table = b"".join(struct.pack("<I", o) for o in offsets)
    body = header + offset_table
    body += b"\x00" * (strings_start - 8 - len(body))
    body += blob
    return struct.pack("<HHI", 0x0001, header_size, strings_start + len(blob)) + body


def _start_element(name_idx: int, attrs: list[tuple[int, int, int, int]]) -> bytes:
    """Build a real ResXMLTree start-element chunk. attrs = (nameIdx, rawIdx, type, data).

    Layout: ResChunk_header(8) + comment(4) + pad(4)  [= headerSize 16]
    then attrExt: ns(4) name(4) attributeStart(2) attributeSize(2)
    attributeCount(2) idIndex(2) classIndex(2) styleIndex(2), then the
    attributes. attributeStart is relative to the start of attrExt.
    """
    attr_size, attr_count = 20, len(attrs)
    attr_start = 20  # attributes begin right after styleIndex
    node = struct.pack("<i", -1) + b"\x00\x00\x00\x00"  # comment + padding
    ext = (
        struct.pack("<I", 0xFFFFFFFF)  # ns
        + struct.pack("<I", name_idx)  # name
        + struct.pack("<H", attr_start)
        + struct.pack("<H", attr_size)
        + struct.pack("<H", attr_count)
        + struct.pack("<H", 0)  # idIndex
        + struct.pack("<H", 0)  # classIndex
        + struct.pack("<H", 0)  # styleIndex
    )
    assert len(ext) == attr_start, f"attributeStart {attr_start} != attrExt {len(ext)}"
    body = node + ext
    for name_i, raw_i, dtype, data in attrs:
        # ResXMLTree_attribute: ns, name, rawValue, then Res_value
        # (size u16, res0 u8, dataType u8, data u32) -- exactly 20 bytes.
        body += (
            struct.pack("<i", -1)  # ns
            + struct.pack("<i", name_i)  # name
            + struct.pack("<i", raw_i)  # rawValue
            + struct.pack("<H", 8)  # Res_value size
            + struct.pack("<B", 0)  # res0
            + struct.pack("<B", dtype)  # dataType
            + struct.pack("<I", data)
        )
    header_size = 16
    return struct.pack("<HHI", 0x0102, header_size, header_size + len(body)) + body


def build_manifest(version_code: int, version_name: str) -> bytes:
    """A minimal but structurally real binary AndroidManifest.xml."""
    strings = [
        "manifest",
        "versionCode",
        "versionName",
        version_name,
        "com.jarvisos.app",
    ]
    out = struct.pack("<I", 0x00080003) + struct.pack("<I", 20)  # file header
    out += _string_pool(strings)
    name_code = strings.index("versionCode")
    name_ver = strings.index("versionName")
    ver_str = strings.index(version_name)
    out += _start_element(
        0,
        [
            (name_code, -1, 0x10, version_code),
            (name_ver, ver_str, 0x03, ver_str),
        ],
    )
    return out


def make_apk(path, version_code: int, version_name: str) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AndroidManifest.xml", build_manifest(version_code, version_name))
        zf.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 64)
        zf.writestr("assets/public/index.html", b"<html></html>")
    path.write_bytes(buf.getvalue())


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


class TestParseManifest:
    def test_reads_version_code_and_name(self):
        info = parse_manifest(build_manifest(23, "1.4.12"))
        assert info["version_code"] == 23
        assert info["version_name"] == "1.4.12"

    def test_never_guesses_a_missing_version(self):
        """A manifest with no versionCode must come back None, not 0."""
        strings = ["manifest", "versionName", "1.0.0"]
        out = struct.pack("<I", 0x00080003) + struct.pack("<I", 20)
        out += _string_pool(strings)
        out += _start_element(0, [(1, 2, 0x03, 2)])
        info = parse_manifest(out)
        assert info["version_code"] is None
        assert info["version_name"] == "1.0.0"

    def test_survives_garbage(self):
        info = parse_manifest(b"not a manifest at all")
        assert info["version_code"] is None

    def test_survives_truncated_manifest(self):
        full = build_manifest(23, "1.4.12")
        info = parse_manifest(full[: len(full) // 2])
        assert info["version_code"] is None

    def test_large_version_code_is_not_mangled(self):
        """versionCode is a u32; reading it as a signed short truncates."""
        info = parse_manifest(build_manifest(2_000_000_003, "9.9.9"))
        assert info["version_code"] == 2_000_000_003


class TestReadApkVersion:
    def test_reads_from_a_real_zip(self, tmp_path):
        apk = tmp_path / "app-debug.apk"
        make_apk(apk, 22, "1.4.12")
        info = read_apk_version(apk)
        assert info["version_code"] == 22
        assert info["version_name"] == "1.4.12"

    def test_returns_none_for_a_corrupt_apk(self, tmp_path):
        apk = tmp_path / "app-debug.apk"
        apk.write_bytes(b"this is not a zip file")
        assert read_apk_version(apk) is None

    def test_returns_none_when_the_manifest_is_missing(self, tmp_path):
        apk = tmp_path / "app-debug.apk"
        with zipfile.ZipFile(apk, "w") as zf:
            zf.writestr("classes.dex", b"dex")
        assert read_apk_version(apk) is None

    def test_returns_none_for_a_missing_file(self, tmp_path):
        assert read_apk_version(tmp_path / "nope.apk") is None

    def test_caches_by_mtime_and_size(self, tmp_path):
        apk = tmp_path / "app-debug.apk"
        make_apk(apk, 22, "1.4.12")
        assert read_apk_version(apk)["version_code"] == 22
        # Republishing a different build must not serve the stale answer.
        make_apk(apk, 23, "1.4.13")
        import os

        os.utime(apk, (0, 0))
        assert read_apk_version(apk)["version_code"] == 23
