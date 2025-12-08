# Troubleshooting Guide: Volume Control Timeout Issues

## Problem Summary
Volume control commands are timing out when sent to the RAG API. The application receives requests but hangs during processing.

## Root Cause Analysis

### Symptoms
- `test_volume.py` runs but gets empty responses or timeouts
- Application receives request but hangs when processing
- Timeout occurs during request processing

### Diagnosis
The timeout is likely caused by **Ollama connectivity or model loading issues**:

1. **Ollama Location**: `http://192.168.1.161:11434`
2. **RAG Server**: `192.168.2.211`
3. **Issue**: If `.1.161` is offline or unreachable from `.2.211`, requests will timeout

### Test Results
Running `tools/test_connectivity.py` shows:
- ✅ Ollama basic endpoints (tags, version) work
- ❌ Ollama generation endpoint times out
- ❌ RAG API times out

This suggests:
- Network connectivity exists (basic endpoints work)
- Model generation is failing (model may need loading or is stuck)

## Solutions

### 1. Verify Ollama Connectivity from RAG Server

SSH into the RAG server and test connectivity:

```bash
ssh jeremiah@192.168.2.211
cd /home/jeremiah/SharedLLM

# Test basic connectivity
curl http://192.168.1.161:11434/api/tags

# Test generation (may take time if model needs loading)
curl -X POST http://192.168.1.161:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:latest",
    "prompt": "Say OK",
    "stream": false,
    "options": {"num_predict": 5}
  }'
```

### 2. Check if Ollama Moved to New Server

If Ollama is now running on the same server as RAG (192.168.2.211), update `.env`:

```bash
# In .env file
OLLAMA_URL=http://localhost:11434
# OR if Ollama is on the same network but different IP
OLLAMA_URL=http://192.168.2.211:11434
```

### 3. Pre-warm the Model

If the model needs to be loaded, the first request will be slow. Pre-warm it:

```bash
# From RAG server
curl -X POST http://192.168.1.161:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:latest",
    "prompt": "test",
    "stream": false
  }'
```

### 4. Check Model Status

Check if the model is already loaded:

```bash
curl http://192.168.1.161:11434/api/ps
```

### 5. Increase Timeout (Temporary Workaround)

If model loading is slow, temporarily increase timeout in `.env`:

```bash
OLLAMA_TIMEOUT=600  # 10 minutes
```

**Note**: This is a workaround. The real fix is to ensure Ollama is accessible and models are loaded.

## Diagnostic Tools

### Run Connectivity Test
```bash
python3 tools/test_connectivity.py
```

This will test:
- Ollama connectivity
- Home Assistant connectivity  
- RAG API responsiveness

### Test Volume Control
```bash
python3 tools/test_volume.py
```

### Test from Server Side
```bash
# Copy test script to server
scp tools/test_ollama_from_server.sh jeremiah@192.168.2.211:/home/jeremiah/SharedLLM/

# SSH and run
ssh jeremiah@192.168.2.211
cd /home/jeremiah/SharedLLM
bash test_ollama_from_server.sh
```

## Deployment Checklist

When deploying to fix timeout issues:

1. ✅ Verify `.env` has correct `OLLAMA_URL`
2. ✅ Run `./deploy_remote.sh` to sync code + config
3. ✅ Test connectivity: `python3 tools/test_connectivity.py`
4. ✅ Pre-warm model if needed
5. ✅ Test volume control: `python3 tools/test_volume.py`

## Network Configuration Reference

- **RAG Server / App Host**: `192.168.2.211` (Port 11435 for API)
- **Home Assistant**: `https://ha.sumemail.com` (configured in .env as HA_URL)
- **Ollama**: `http://192.168.1.161:11434` (configured in .env as OLLAMA_URL)

## Next Steps

1. **Immediate**: Test Ollama connectivity from RAG server
2. **If unreachable**: Update `OLLAMA_URL` in `.env` if Ollama moved
3. **If reachable but slow**: Pre-warm model, check model loading status
4. **If still timing out**: Check server logs for detailed error messages

## Related Files

- `tools/test_connectivity.py` - Connectivity diagnostic tool
- `tools/test_volume.py` - Volume control test script
- `tools/test_ollama_from_server.sh` - Server-side Ollama test
- `app/logic/utils.py` - Ollama call implementation (line 131)
- `app/settings.py` - OLLAMA_URL and OLLAMA_TIMEOUT configuration

