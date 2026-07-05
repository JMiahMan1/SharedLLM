# services/execution/handlers/ha_config.py
"""
Home Assistant configuration inspection via WebSocket API.
Allows diagnosing misconfigured integrations without treating HA as a black box.
"""
import logging
import json
try:
    from schemas import ExecutionResult
except ImportError:
    from ..schemas import ExecutionResult

import aiohttp

log = logging.getLogger("execution.ha_config")


async def _get_ha_credentials(user_context: dict) -> tuple:
    """Resolve HA URL and token from identity service."""
    from services.config import INTERNAL_SECRET, IDENTITY_SVC_URL

    rag_user = user_context.get("user", "default")
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{IDENTITY_SVC_URL}/api/resolve",
            json={"rag_user": rag_user},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        if resp.status_code == 200:
            creds = resp.json()
            return creds.get("ha_url"), creds.get("ha_token")
    return None, None


async def _ws_request(ha_url: str, token: str, message: dict) -> dict:
    """Send a single request via HA WebSocket and return the result."""
    try:
        import websockets
        import ssl
    except ImportError:
        return {"error": "websockets package not available"}

    ws_url = ha_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/api/websocket"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(ws_url, ssl=ssl_ctx) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            return {"error": f"Unexpected greeting: {hello}"}

        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth_resp = json.loads(await ws.recv())
        if auth_resp.get("type") != "auth_ok":
            return {"error": f"Auth failed: {auth_resp}"}

        await ws.send(json.dumps(message))
        resp = json.loads(await ws.recv())
        return resp


async def handle_ha_config(req_data: dict) -> ExecutionResult:
    """Inspect Home Assistant integration configurations."""
    user_context = req_data.get("user_context", {})
    action = req_data.get("action", "list_integrations")
    domain = req_data.get("domain")
    entity_domain = req_data.get("entity_domain")
    keyword = req_data.get("keyword")

    ha_url, ha_token = await _get_ha_credentials(user_context)
    if not ha_url or not ha_token:
        return ExecutionResult(
            status="FAILURE",
            message="Could not resolve Home Assistant credentials. Ensure HA URL and token are configured in identity settings.",
            service="ha_config"
        )

    try:
        if action == "list_integrations":
            resp = await _ws_request(ha_url, ha_token, {"id": 1, "type": "config_entries/get"})
            if not resp.get("success"):
                return ExecutionResult(status="FAILURE", message=f"WebSocket error: {resp.get('error')}", service="ha_config")

            entries = resp.get("result", [])
            if keyword:
                kw = keyword.lower()
                entries = [e for e in entries if kw in e.get("domain", "").lower() or kw in e.get("title", "").lower()]

            summary = []
            for e in entries:
                summary.append({
                    "domain": e.get("domain"),
                    "title": e.get("title"),
                    "state": e.get("state"),
                    "entry_id": e.get("entry_id"),
                    "supports_reconfigure": e.get("supports_reconfigure"),
                    "subentry_types": e.get("supported_subentry_types", {}),
                    "num_subentries": e.get("num_subentries", 0),
                })

            return ExecutionResult(
                status="SUCCESS",
                message=f"Found {len(summary)} integrations" + (f" matching '{keyword}'" if keyword else ""),
                service="ha_config",
                detail={"integrations": summary}
            )

        elif action == "get_integration":
            if not domain:
                return ExecutionResult(status="FAILURE", message="'domain' is required for get_integration action", service="ha_config")

            resp = await _ws_request(ha_url, ha_token, {"id": 1, "type": "config_entries/get"})
            if not resp.get("success"):
                return ExecutionResult(status="FAILURE", message=f"WebSocket error: {resp.get('error')}", service="ha_config")

            entries = resp.get("result", [])
            matching = [e for e in entries if e.get("domain") == domain]
            if not matching:
                return ExecutionResult(status="SUCCESS", message=f"No integrations found for domain '{domain}'", service="ha_config")

            # Note: HA WebSocket API does not expose the actual 'data' field for security reasons.
            # The 'title' often contains the configured URL/server address.
            # To change settings, use the HA UI Settings -> Devices & Services -> [Integration] -> Configure.
            result_entries = []
            for e in matching:
                result_entries.append({
                    "entry_id": e.get("entry_id"),
                    "title": e.get("title"),
                    "state": e.get("state"),
                    "source": e.get("source"),
                    "supports_reconfigure": e.get("supports_reconfigure"),
                    "supported_subentry_types": e.get("supported_subentry_types", {}),
                    "num_subentries": e.get("num_subentries", 0),
                    "disabled_by": e.get("disabled_by"),
                    "reason": e.get("reason"),
                    "note": "HA does not expose actual config data via API. Use HA UI to modify settings.",
                })

            return ExecutionResult(
                status="SUCCESS",
                message=f"Found {len(result_entries)} entry(ies) for '{domain}'",
                service="ha_config",
                detail={"entries": result_entries}
            )

        elif action == "get_entities":
            resp = await _ws_request(ha_url, ha_token, {"id": 1, "type": "config/entity_registry/list"})
            if not resp.get("success"):
                return ExecutionResult(status="FAILURE", message=f"WebSocket error: {resp.get('error')}", service="ha_config")

            entities = resp.get("result", [])
            if entity_domain:
                entities = [e for e in entities if e.get("entity_id", "").startswith(entity_domain + ".")]
            if keyword:
                kw = keyword.lower()
                entities = [e for e in entities if kw in e.get("entity_id", "").lower() or kw in e.get("name", "").lower() or kw in e.get("platform", "").lower()]

            summary = []
            for e in entities[:100]:
                summary.append({
                    "entity_id": e.get("entity_id"),
                    "name": e.get("name"),
                    "platform": e.get("platform"),
                    "config_entry_id": e.get("config_entry_id"),
                    "disabled_by": e.get("disabled_by"),
                })

            return ExecutionResult(
                status="SUCCESS",
                message=f"Found {len(summary)} entities" + (f" for domain '{entity_domain}'" if entity_domain else "") + (f" matching '{keyword}'" if keyword else ""),
                service="ha_config",
                detail={"entities": summary}
            )

        elif action == "get_config":
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(f"{ha_url}/api/config", headers={"Authorization": f"Bearer {ha_token}"})
                if resp.status_code != 200:
                    return ExecutionResult(status="FAILURE", message=f"REST API error: {resp.status_code} {resp.text}", service="ha_config")

                config = resp.json()
                summary = {
                    "ha_version": config.get("version"),
                    "location_name": config.get("location_name"),
                    "time_zone": config.get("time_zone"),
                  "components_count": len(config.get("components", [])),
                   "components": sorted(config.get("components") or []) if isinstance(config.get("components"), (list, tuple)) else [],
                }
                if keyword:
                    kw = keyword.lower()
                    filtered = [c for c in summary["components"] if kw in c.lower()]  # pyright: ignore[reportGeneralTypeIssues]
                    summary["components"] = filtered
                    summary["components_count"] = len(filtered)

                return ExecutionResult(
                    status="SUCCESS",
                    message=f"HA config retrieved (v{config.get('version')})",
                    service="ha_config",
                    detail=summary
                )

        else:
            return ExecutionResult(status="FAILURE", message=f"Unknown action: {action}", service="ha_config")

    except Exception as e:
        log.error(f"HA config inspection failed: {e}")
        return ExecutionResult(status="FAILURE", message=f"HA config error: {e}", service="ha_config")
