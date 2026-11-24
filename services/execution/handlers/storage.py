# services/execution/handlers/storage.py
import asyncio
import logging

from services.execution.schemas import ExecutionResult, StorageFileReadRequest, StorageFileWriteRequest, StorageTextToAudioRequest

from ..artifacts import artifact_name, create_artifact
from ..http_client import fetch_bytes
from ..http_client import request as http_request
from ..nextcloud_client import resolve_credentials, webdav_url
from ..pdf_text import DEFAULT_MAX_CHARS, PdfReadError, extract_pdf_text, is_pdf
from ..personal_data import resolve_personal_data_provider
from ..tts import text_to_speech

log = logging.getLogger("execution.storage")

#: A growth-journal photo larger than this is refused rather than pushed at Nextcloud.
MAX_PHOTO_BYTES = 25 * 1024 * 1024
#: What one read of a PDF returns to the model at most (the rest via start_page).
PDF_READ_MAX_CHARS = DEFAULT_MAX_CHARS

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="storage", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="storage", detail=detail)

def _pdf_result(req: StorageFileReadRequest, data: bytes) -> ExecutionResult:
    """A PDF's text, page by page, with the pages it holds ("pages 1-6 of 23")."""
    name = req.path.rstrip("/").rsplit("/", 1)[-1]
    try:
        pdf = extract_pdf_text(data, start_page=req.start_page, max_chars=PDF_READ_MAX_CHARS, fallback_title=name)
    except PdfReadError as e:
        return _fail(f"Could not read the PDF storage:{req.path}: {e}")
    except ValueError as e:
        return _fail(str(e))
    detail = pdf.as_detail()
    detail["path"] = req.path
    message = f"Read PDF storage:{req.path} ({pdf.pages_label})"
    if pdf.scanned:
        message += ". It is a scanned image with no text layer."
    return _ok(message, detail)


async def handle_storage_read(req: StorageFileReadRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
        assert url is not None and user is not None and pw is not None

        file_url = webdav_url(url, user, req.path)
        # Bytes, not text: a PDF decoded as text is binary garbage.
        status, content_type, data = await fetch_bytes(file_url, auth=(user, pw), timeout=30, verify=False)

        if status == 200:
            if is_pdf(content_type, data):
                # Off the event loop: a long PDF takes a while to parse.
                return await asyncio.to_thread(_pdf_result, req, data)
            content = data.decode("utf-8", errors="replace")
            return _ok(f"Read {len(content)} bytes from storage:{req.path}", {"content": content, "path": req.path})
        elif status == 404:
            return _fail(f"File not found in storage: {req.path}")
        else:
            return _fail(f"Nextcloud error {status}: {data[:200].decode('utf-8', errors='replace')}")

    except Exception as e:
        log.error(f"Storage read failed: {e}")
        return _fail(str(e))

async def handle_storage_write(req: StorageFileWriteRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
        assert url is not None and user is not None and pw is not None

        file_url = webdav_url(url, user, req.path)
        # Ensure parent directories exist (Nextcloud WebDAV doesn't do this automatically with PUT)
        # For simplicity in this handler, we'll just try the PUT
        resp = await http_request("PUT", file_url, auth=(user, pw), data=req.content, timeout=30, verify=False)

        if resp["status_code"] in (200, 201, 204):
            return _ok(f"Successfully wrote to storage:{req.path}")
        else:
            return _fail(f"Nextcloud error {resp['status_code']}: {resp['text'][:200]}")

    except Exception as e:
        log.error(f"Storage write failed: {e}")
        return _fail(str(e))

async def handle_photo_upload(req) -> ExecutionResult:
    """Store a photo in Nextcloud, optionally returning a JPEG thumbnail.

    The Garden hub keeps its growth journal in ``Garden/Photos/<plant>/`` and has
    no Nextcloud credentials of its own: the bytes come through here, and the one
    image stack that already depends on Pillow makes the small copy it serves.
    """
    import base64
    import binascii
    import mimetypes

    try:
        data = base64.b64decode(req.data or "", validate=True)
    except (binascii.Error, ValueError, TypeError):
        return _fail("The photo was not readable as base64.")
    if not data:
        return _fail("The photo is empty.")
    if len(data) > MAX_PHOTO_BYTES:
        limit = MAX_PHOTO_BYTES // (1024 * 1024)
        return _fail(f"Photos can be up to {limit} MB.")

    folder = (req.folder or "").strip().strip("/")
    if not folder:
        return _fail("A folder inside Nextcloud is required (e.g. Garden/Photos/<plant_id>).")

    # The thumbnail is made whether or not Nextcloud exists: the journal's grid
    # is what people actually look at, and a garden without Nextcloud still gets
    # the photo on the plant's timeline (the hub keeps the bytes as a warning).
    thumb = _jpeg_thumbnail(data, req.thumbnail_max_px) if req.thumbnail_max_px else None
    thumb_detail: dict = {}
    if req.thumbnail_max_px:
        if thumb is None:
            thumb_detail["thumbnail_warning"] = "The photo could not be resized; no thumbnail was made."
        else:
            thumb_detail["thumbnail_base64"] = base64.b64encode(thumb).decode("ascii")
            thumb_detail["thumbnail_bytes"] = len(thumb)

    provider = resolve_personal_data_provider(req.user_context)
    if provider is None:
        return _ok(
            "The photo was received but Nextcloud is not connected, so only the thumbnail was kept.",
            detail={
                "path": None,
                "name": None,
                "size": len(data),
                "warning": "No Nextcloud account is connected, so the full photo was not stored.",
                **thumb_detail,
            },
        )
    name = provider.sanitize_filename(req.filename or "photo.jpg", "photo.jpg")
    path = f"{folder}/{name}"
    try:
        await provider.ensure_directory(folder)
        if not await provider.upload_file(path, data, req.content_type or mimetypes.guess_type(name)[0] or "image/jpeg"):
            return _ok(
                "The photo could not be stored in Nextcloud; only the thumbnail was kept.",
                detail={
                    "path": None, "name": None, "size": len(data),
                    "warning": "The full photo was not stored in Nextcloud.",
                    **thumb_detail,
                },
            )
    except Exception as exc:
        log.error("Photo upload failed: %s", exc)
        return _ok(
            "The photo could not be stored in Nextcloud; only the thumbnail was kept.",
            detail={
                "path": None, "name": None, "size": len(data),
                "warning": f"The full photo was not stored in Nextcloud ({exc}).",
                **thumb_detail,
            },
        )

    detail: dict = {"path": path, "name": name, "size": len(data), **thumb_detail}
    return _ok(f"Stored {path}.", detail=detail)


def _jpeg_thumbnail(data: bytes, max_px: int) -> bytes | None:
    """A JPEG no larger than ``max_px`` on its long side, or None if it will not decode."""
    try:
        from io import BytesIO

        from PIL import Image, ImageOps

        image = Image.open(BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((max_px, max_px))
        out = BytesIO()
        image.save(out, format="JPEG", quality=82)
        return out.getvalue()
    except Exception as exc:
        log.info("Thumbnail not made: %s", exc)
        return None


async def handle_storage_tts(req: StorageTextToAudioRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
        assert url is not None and user is not None and pw is not None

        # 1. Read input file
        input_url = webdav_url(url, user, req.input_path)
        log.info(f"[storage_tts] Reading input: {req.input_path}")
        resp = await http_request("GET", input_url, auth=(user, pw), timeout=30, verify=False)
        if resp["status_code"] != 200:
            return _fail(f"Failed to read input file ({resp['status_code']})")

        text = resp["text"]
        if not text.strip():
            return _fail("Input file is empty.")

        # 2. Generate Audio
        log.info(f"[storage_tts] Generating audio (storybook={req.storybook}, voice={req.voice})")
        audio_bytes = await text_to_speech(text, voice=req.voice, storybook=req.storybook)
        if not audio_bytes:
            return _fail("TTS generation returned empty bytes")

        # 3. Write output file
        out_path = req.output_path
        if not out_path:
            # Default to same name with .wav
            base_path = req.input_path.rsplit(".", 1)[0]
            out_path = f"{base_path}.wav"

        output_url = webdav_url(url, user, out_path)
        log.info(f"[storage_tts] Writing output: {out_path}")
        put_resp = await http_request("PUT", output_url, auth=(user, pw), data=audio_bytes, timeout=60, verify=False)

        if put_resp["status_code"] in (200, 201, 204):
            detail = {"path": out_path, "size": len(audio_bytes)}
            artifact = await create_artifact(
                user_context=req.user_context,
                kind="tts_audio",
                name=artifact_name(out_path, "audio.wav"),
                data=audio_bytes,
                mime_type="audio/wav",
                workspace_id=req.workspace_id,
                generator="storage_tts",
                metadata={
                    "input_path": req.input_path,
                    "output_path": out_path,
                    "voice": req.voice,
                    "storybook": req.storybook,
                },
            )
            if artifact:
                detail["artifact"] = artifact
            return _ok(f"Successfully converted {req.input_path} to audio at {out_path}", detail)
        else:
            return _fail(f"Failed to write output audio ({put_resp['status_code']}): {put_resp['text'][:200]}")

    except Exception as e:
        log.error(f"Storage TTS failed: {e}")
        return _fail(str(e))
