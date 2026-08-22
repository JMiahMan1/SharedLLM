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
    from schemas import EsphomeRequest, ExecutionResult
except ImportError:
    from .. import esphome_client
    from ..schemas import EsphomeRequest, ExecutionResult  # type: ignore[attr-defined]

log = logging.getLogger("execution.esphome")


async def handle_esphome(req: EsphomeRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(
        f"[esphome] user={ctx.user} device={req.device} "
        f"action={req.action} entity={req.entity}"
    )
    service = "esphome"
    try:
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
    except esphome_client.EsphomeConfigError as e:
        log.warning(f"[esphome] config error: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service=service)
    except ValueError as e:
        return ExecutionResult(status="FAILURE", message=str(e), service=service)
    except TimeoutError:
        return ExecutionResult(
            status="FAILURE",
            message=(
                f"Timed out connecting to ESPHome device '{req.device}' "
                f"after {esphome_client._CONNECT_TIMEOUT_SECONDS}s."
            ),
            service=service,
        )
    except aioesphomeapi.APIConnectionError as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not talk to ESPHome device '{req.device}': {e}",
            service=service,
        )
