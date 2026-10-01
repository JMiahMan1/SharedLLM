"""Read versionCode/versionName out of an APK's binary AndroidManifest.xml.

`aapt`/`apkanalyzer` are not available in the gateway image, and reading the
number from `build.gradle` instead is what caused a real bug: the advertised
`apk_version_code` drifted ahead of the APK actually being served, so a user
who installed the newest available build was told to update forever. The
artifact we serve is the only trustworthy source for its own version.

Pure stdlib (`zipfile` + `struct`) so it runs anywhere the gateway does.
"""
import hashlib
import logging
import struct
import zipfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RES_STRING_POOL_TYPE = 0x0001
RES_XML_START_ELEMENT_TYPE = 0x0102
UTF8_FLAG = 0x100
TYPE_STRING = 0x03

# The string pool is tiny next to the APK, but this endpoint is polled by every
# client, so cache on (path, mtime, size) rather than re-opening a 12MB zip.
# Values are version info dicts, digest strings, or None.
_cache: dict[tuple, object] = {}


def _read_string_pool(data: bytes, chunk_start: int) -> list[str]:
    """Decode a ResStringPool chunk, or [] if it is truncated/unsound.

    A half-written APK is a real possibility (an interrupted upload), so every
    read is bounds-checked: this returns what it can rather than raising, and
    the caller treats a short pool as "version unknown".
    """
    if chunk_start + 28 > len(data):
        return []
    # ResChunk_header is 8 bytes, then stringCount, styleCount, flags,
    # stringsStart, stylesStart -- i.e. the five fields begin at
    # chunk_start + 8. The offset table follows them; headerSize is 28.
    try:
        string_count, _style_count, flags, strings_start, _styles_start = struct.unpack_from(
            "<IIIII", data, chunk_start + 8
        )
    except struct.error:
        return []
    if string_count > len(data):  # absurd count means the header is nonsense
        return []

    off = chunk_start + 28
    offsets = []
    for _ in range(string_count):
        if off + 4 > len(data):
            return []
        offsets.append(struct.unpack_from("<I", data, off)[0])
        off += 4

    utf8 = bool(flags & UTF8_FLAG)
    out: list[str] = []
    for o in offsets:
        p = chunk_start + strings_start + o
        if p < 0 or p >= len(data):
            out.append("")
            continue
        try:
            if utf8:
                # Two varint-length-prefixed values, then the UTF-8 bytes.
                n1 = data[p]
                p += 1
                if n1 & 0x80:
                    p += 1
                if p >= len(data):
                    out.append("")
                    continue
                n = data[p]
                p += 1
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | data[p]
                    p += 1
                out.append(data[p:p + n].decode("utf-8", errors="replace"))
            else:
                nchars = struct.unpack_from("<H", data, p)[0]
                out.append(data[p + 2:p + 2 + nchars * 2].decode("utf-16-le", errors="replace"))
        except (IndexError, struct.error):
            out.append("")
    return out


def parse_manifest(data: bytes) -> dict:
    """Return {'version_code': int|None, 'version_name': str|None} from a
    binary AndroidManifest.xml. Missing values come back as None rather than a
    default, so callers can tell "not there" from "version 0"."""
    strings: list[str] = []
    found: dict = {}

    off = 8  # skip the file header (magic + size)
    while off + 8 <= len(data):
        try:
            chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, off)
        except struct.error:
            break
        if chunk_size == 0:
            break
        if chunk_type == RES_STRING_POOL_TYPE:
            strings = _read_string_pool(data, off)
        elif chunk_type == RES_XML_START_ELEMENT_TYPE:
            ext = off + header_size
            if ext + 16 > len(data):
                break
            try:
                attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, ext + 8)
            except struct.error:
                break
            for i in range(attr_count):
                a = ext + attr_start + i * attr_size
                if a + 20 > len(data) or attr_size < 20:
                    break
                attr_name = struct.unpack_from("<i", data, a + 4)[0]
                if not (0 <= attr_name < len(strings)):
                    continue
                name = strings[attr_name]
                if name not in ("versionCode", "versionName"):
                    continue
                # ResXMLTree_attribute = ns, name, rawValue, Res_value; the
                # Res_value (size u16, res0 u8, dataType u8, data u32) starts
                # at +12, so dataType is at +15 and data at +16.
                data_type = data[a + 15]
                typed = struct.unpack_from("<I", data, a + 16)[0]
                if name == "versionCode":
                    found["version_code"] = typed
                elif data_type == TYPE_STRING:
                    raw = struct.unpack_from("<i", data, a + 8)[0]
                    found["version_name"] = strings[raw] if 0 <= raw < len(strings) else None
                else:
                    found["version_name"] = str(typed)
        off += chunk_size

    return {
        "version_code": found.get("version_code"),
        "version_name": found.get("version_name"),
    }


def read_apk_version(apk_path: Path) -> Optional[dict]:
    """Version info for a published APK, or None if it cannot be determined.

    None is a real answer, not a default: it means "we do not know what we are
    serving", and callers must surface that rather than invent a number.
    """
    try:
        st = apk_path.stat()
    except OSError:
        return None
    key = (str(apk_path), st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]

    result: Optional[dict] = None
    try:
        with zipfile.ZipFile(apk_path) as zf:
            data = zf.read("AndroidManifest.xml")
        parsed = parse_manifest(data)
        if parsed.get("version_code") is not None:
            result = parsed
    except Exception as e:  # corrupt zip, missing manifest, truncated upload
        log.warning("[AppUpdates] Could not read version from %s: %s", apk_path.name, e)
        result = None

    _cache[key] = result
    return result


def read_apk_digest(apk_path: Path) -> Optional[str]:
    """SHA-256 of the APK we are actually serving, hex-encoded, or None.

    The client verifies the download against this before handing the file to
    the system installer, so the bytes that get installed are provably the
    bytes we published. None means "we could not hash it", and the client must
    treat that as a refusal to install rather than a licence to skip the check.
    """
    try:
        st = apk_path.stat()
    except OSError:
        return None
    key = ("sha256", str(apk_path), st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]

    digest: Optional[str] = None
    try:
        h = hashlib.sha256()
        with open(apk_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 256), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError as e:
        log.warning("[AppUpdates] Could not hash %s: %s", apk_path.name, e)

    _cache[key] = digest
    return digest


def clear_cache() -> None:
    _cache.clear()
