import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import VOLUME_MANIFEST_PATH, VOLUME_BACKUP_ROOT

log = logging.getLogger("execution.volumes")

DEFAULT_VOLUME_MANIFEST_PATH = VOLUME_MANIFEST_PATH or os.path.join(os.path.expanduser("~/workspace"), "config/volumes.json")
DEFAULT_BACKUP_ROOT = VOLUME_BACKUP_ROOT or "/var/backups/sharedllm"


def _get_docker_client():
    try:
        import docker

        return docker.from_env()
    except ImportError as exc:
        raise RuntimeError("The 'docker' Python SDK is not installed.") from exc
    except Exception as exc:
        raise RuntimeError(f"Could not connect to Docker socket: {exc}") from exc


def _load_manifest() -> dict[str, Any]:
    path = Path(DEFAULT_VOLUME_MANIFEST_PATH)
    if not path.exists():
        return {"volumes": []}
    data = json.loads(path.read_text())
    volumes = data.get("volumes", [])
    return {"volumes": volumes if isinstance(volumes, list) else []}


def _format_bytes(size: int | None) -> str:
    if size is None:
        return "unknown"
    value = float(size)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def _backup_command(volume_name: str) -> str:
    return (
        "docker run --rm "
        f"-v {volume_name}:/volume:ro "
        f"-v {DEFAULT_BACKUP_ROOT}:/backup "
        f"alpine sh -lc 'tar czf /backup/{volume_name}-$(date +%Y%m%d-%H%M%S).tgz -C /volume .'"
    )


def _prune_command(volume_name: str) -> str:
    return f"docker volume rm {volume_name}"


async def handle_volumes(req) -> dict[str, Any]:
    user_context = getattr(req, "user_context", None)
    is_admin = bool(getattr(user_context, "is_admin", False))
    if not is_admin:
        return {
            "status": "FAILURE",
            "message": "Volume inventory requires admin privileges.",
            "service": "volumes",
            "detail": {"error": "insufficient_permissions"},
        }

    try:
        client = _get_docker_client()
        manifest = _load_manifest()
        df_data = client.df()
        docker_volumes = {vol.name: vol for vol in client.volumes.list()}
    except Exception as exc:
        log.error("Failed to inspect Docker volumes: %s", exc)
        return {
            "status": "FAILURE",
            "message": f"Failed to inspect Docker volumes: {exc}",
            "service": "volumes",
            "detail": {"error": "inspection_failed"},
        }

    usage_map: dict[str, dict[str, Any]] = {}
    for item in df_data.get("Volumes", []) or []:
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        usage = item.get("UsageData") or {}
        usage_map[name] = {
            "size_bytes": usage.get("Size"),
            "ref_count": usage.get("RefCount"),
            "links": item.get("Links") or 0,
        }

    items: list[dict[str, Any]] = []
    for entry in manifest["volumes"]:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        docker_obj = docker_volumes.get(name)
        usage = usage_map.get(name, {})
        size_bytes = usage.get("size_bytes")
        items.append(
            {
                "name": name,
                "service": entry.get("service"),
                "mount_path": entry.get("mount_path"),
                "category": entry.get("category"),
                "criticality": entry.get("criticality"),
                "rebuildable": bool(entry.get("rebuildable", False)),
                "backup_policy": entry.get("backup_policy"),
                "notes": entry.get("notes"),
                "exists": docker_obj is not None,
                "mountpoint": (docker_obj.attrs.get("Mountpoint") if docker_obj else None),
                "created_at": (docker_obj.attrs.get("CreatedAt") if docker_obj else None),
                "labels": (docker_obj.attrs.get("Labels") if docker_obj else {}),
                "ref_count": usage.get("ref_count"),
                "size_bytes": size_bytes,
                "size_human": _format_bytes(size_bytes),
                "backup_example": _backup_command(name),
                "prune_example": _prune_command(name),
            }
        )

    discovered_names = set(docker_volumes.keys())
    manifest_names = {item["name"] for item in items}
    unmanaged = []
    for name in sorted(discovered_names - manifest_names):
        usage = usage_map.get(name, {})
        size_bytes = usage.get("size_bytes")
        unmanaged.append(
            {
                "name": name,
                "service": "untracked",
                "mount_path": None,
                "category": "untracked",
                "criticality": "unknown",
                "rebuildable": None,
                "backup_policy": "review",
                "notes": "Volume exists in Docker but is not tracked in config/volumes.json.",
                "exists": True,
                "mountpoint": docker_volumes[name].attrs.get("Mountpoint"),
                "created_at": docker_volumes[name].attrs.get("CreatedAt"),
                "labels": docker_volumes[name].attrs.get("Labels") or {},
                "ref_count": usage.get("ref_count"),
                "size_bytes": size_bytes,
                "size_human": _format_bytes(size_bytes),
                "backup_example": _backup_command(name),
                "prune_example": _prune_command(name),
            }
        )

    all_items = items + unmanaged
    total_bytes = sum(item["size_bytes"] or 0 for item in all_items)
    critical_bytes = sum(
        item["size_bytes"] or 0 for item in all_items if item.get("criticality") == "critical"
    )

    return {
        "status": "SUCCESS",
        "message": f"Inspected {len(all_items)} Docker volumes.",
        "service": "volumes",
        "detail": {
            "manifest_path": DEFAULT_VOLUME_MANIFEST_PATH,
            "backup_root": DEFAULT_BACKUP_ROOT,
            "total_volumes": len(all_items),
            "tracked_volumes": len(items),
            "unmanaged_volumes": len(unmanaged),
            "total_bytes": total_bytes,
            "total_human": _format_bytes(total_bytes),
            "critical_bytes": critical_bytes,
            "critical_human": _format_bytes(critical_bytes),
            "volumes": all_items,
        },
    }
