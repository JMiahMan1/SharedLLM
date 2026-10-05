# services/execution/handlers/esphome.py
"""Direct ESPHome device control via the native API.

Complements the HA bridge: when Home Assistant is down (or for the
lowest-latency FastPath commands) we can still drive ESPHome devices
directly over their native TCP protocol.
"""

import logging

import aioesphomeapi

try:
    import esphome_client
    from schemas import EsphomePairRequest, EsphomeRequest, ExecutionResult
except ImportError:
    from .. import esphome_client
    from ..schemas import EsphomePairRequest, EsphomeRequest, ExecutionResult  # type: ignore[attr-defined]

log = logging.getLogger("execution.esphome")


def _failure(e: Exception, service: str, target: str) -> ExecutionResult:
    """One mapping from what can go wrong talking to a device to a reply."""
    if isinstance(e, esphome_client.EsphomeConfigError):
        log.warning(f"[esphome] {e}")
        message = str(e)
    elif isinstance(e, TimeoutError):
        message = f"No answer from '{target}' after {esphome_client._CONNECT_TIMEOUT_SECONDS}s. Is it on and awake?"
    elif isinstance(e, aioesphomeapi.APIConnectionError):
        message = f"Could not talk to '{target}': {e}"
    else:
        message = str(e)
    return ExecutionResult(status="FAILURE", message=message, service=service)


async def handle_esphome(req: EsphomeRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(
        f"[esphome] user={ctx.user} device={req.device} "
        f"action={req.action} entity={req.entity}"
    )
    service = "esphome"
    try:
        # A paired device belongs to the user who paired it.
        cfg = await esphome_client.get_device(req.device)
        owner = cfg.get("owner")
        if owner and owner != ctx.user.lower() and not ctx.is_admin:
            return ExecutionResult(
                status="FAILURE",
                message=f"'{req.device}' is linked to another user.",
                service=service,
            )
        if req.action == "list":
            data = await esphome_client.list_entities(req.device)
            names = [f"{e['domain']}/{e['object_id']}" for e in data["entities"]]
            return ExecutionResult(
                status="SUCCESS",
                message=(
                    f"{data['device']['name']} exposes {len(names)} entity(ies): "
                    f"{', '.join(names)}"
                ),
                service=service,
                detail=data,
            )

        if req.action == "configure_jarvis":
            # Hand the device Jarvis's address and a key of its own, so it can
            # call back (step sync, questions) without anything typed on it.
            url = cfg.get("jarvis_url") or await esphome_client.get_jarvis_device_url()
            if not ctx.api_key:
                raise esphome_client.EsphomeConfigError(
                    "No API key in the user context, so a device key cannot be "
                    "minted for this user. Call this as an authenticated user."
                )
            data = await esphome_client.push_jarvis_endpoint(
                req.device,
                url,
                lambda: esphome_client.mint_device_api_key(ctx.api_key, req.device),
            )
            return ExecutionResult(
                status="SUCCESS",
                message=f"'{req.device}' now reaches Jarvis at {url} with its own API key.",
                service=service,
                detail=data,
            )

        # action == "call"
        if not req.entity:
            return ExecutionResult(
                status="FAILURE",
                message="An entity name is required for action 'call'.",
                service=service,
            )
        result = await esphome_client.call_entity(req.device, req.entity, req.params)
        log.info(f"[esphome] command sent: {result}")
        return ExecutionResult(
            status="SUCCESS",
            message=(
                f"Command sent to {result['domain']}/{result['name']} on '{req.device}'."
            ),
            service=service,
            detail=result,
        )
    except (esphome_client.EsphomeConfigError, ValueError, TimeoutError, aioesphomeapi.APIConnectionError) as e:
        return _failure(e, service, req.device)


async def handle_esphome_pair(req: EsphomePairRequest) -> ExecutionResult:
    """Add a companion device as the calling user."""
    ctx = req.user_context
    service = "esphome_pair"
    log.info(f"[esphome] pair step={req.step} user={ctx.user} host={req.host}")
    try:
        if req.step == "discover":
            devices = await esphome_client.discover_devices()
            return ExecutionResult(
                status="SUCCESS",
                message=f"Found {len(devices)} device(s) to add." if devices
                else "No new devices found on the network. Enter the device's address instead.",
                service=service,
                detail={"devices": devices},
            )
        if not req.host or not req.host.strip():
            return ExecutionResult(status="FAILURE", message="The device's address is required.", service=service)
        host = req.host.strip()
        if req.step == "start":
            data = await esphome_client.pair_start(host, req.port)
            msg = (f"Enter the code shown on {data['friendly_name']}." if data["method"] == "code"
                   else f"{data['friendly_name']} has no screen to show a code; it will be linked to you as is.")
            return ExecutionResult(status="SUCCESS", message=msg, service=service, detail=data)
        data = await esphome_client.pair_finish(host, req.port, req.code, ctx.user, ctx.api_key, req.kind, req.jarvis_url)
        msg = f"{data['friendly_name']} is now linked to {ctx.user}."
        if not data["url_sent"]:
            msg += " It does not call Jarvis itself, so no key or address was sent."
        return ExecutionResult(status="SUCCESS", message=msg, service=service, detail=data)
    except (esphome_client.EsphomeConfigError, TimeoutError, aioesphomeapi.APIConnectionError) as e:
        return _failure(e, service, req.host or "the device")
