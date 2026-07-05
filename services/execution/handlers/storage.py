# services/execution/handlers/storage.py
import logging
import requests
from services.execution.schemas import StorageFileReadRequest, StorageFileWriteRequest, StorageTextToAudioRequest, ExecutionResult
from ..tts import text_to_speech
from ..nextcloud_client import resolve_credentials, webdav_url

log = logging.getLogger("execution.storage")

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="storage", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="storage", detail=detail)

async def handle_storage_read(req: StorageFileReadRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
        assert url is not None and user is not None and pw is not None

        file_url = webdav_url(url, user, req.path)
        resp = requests.get(file_url, auth=(user, pw), timeout=30, verify=False)
        
        if resp.status == 200:
            content = resp.text
            return _ok(f"Read {len(content)} bytes from storage:{req.path}", {"content": content, "path": req.path})
        elif resp.status == 404:
            return _fail(f"File not found in storage: {req.path}")
        else:
            return _fail(f"Nextcloud error {resp.status}: {resp.text[:200]}")
            
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
        resp = requests.put(file_url, auth=(user, pw), data=req.content, timeout=30, verify=False)
        
        if resp.status in (200, 201, 204):
            return _ok(f"Successfully wrote to storage:{req.path}")
        else:
            return _fail(f"Nextcloud error {resp.status}: {resp.text[:200]}")
            
    except Exception as e:
        log.error(f"Storage write failed: {e}")
        return _fail(str(e))

async def handle_storage_tts(req: StorageTextToAudioRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
        assert url is not None and user is not None and pw is not None

        # 1. Read input file
        input_url = webdav_url(url, user, req.input_path)
        log.info(f"[storage_tts] Reading input: {req.input_path}")
        resp = requests.get(input_url, auth=(user, pw), timeout=30, verify=False)
        if resp.status != 200:
            return _fail(f"Failed to read input file ({resp.status})")
        
        text = resp.text
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
        put_resp = requests.put(output_url, auth=(user, pw), data=audio_bytes, timeout=60, verify=False)
        
        if put_resp.status in (200, 201, 204):
            return _ok(f"Successfully converted {req.input_path} to audio at {out_path}", {"path": out_path, "size": len(audio_bytes)})
        else:
            return _fail(f"Failed to write output audio ({put_resp.status}): {put_resp.text[:200]}")
            
    except Exception as e:
        log.error(f"Storage TTS failed: {e}")
        return _fail(str(e))
