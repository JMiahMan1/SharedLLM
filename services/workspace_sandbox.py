"""Per-workspace sandbox execution.

Every Raven workspace gets its OWN isolated Docker container. All workspace
commands (shell, git, lint, search) execute INSIDE that container — never on
the host — with only that workspace's directory mounted, a private network, a
non-root user, and resource limits. This is what makes a workspace a "safe
place to run commands that could do damage if run on the host system": a
runaway `rm -rf`, a fork bomb, or a malicious script is contained to the
container and can only touch its own files.

Both the agent loop (execution service) and the IDE (workspace_runtime) import
this module and route their workspace tool calls through ``run_workspace_cmd``,
so they share identical, confined execution semantics.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from typing import Any

from docker.errors import NotFound

import docker

log = logging.getLogger("workspace_sandbox")

CONTAINER_PREFIX = "wsbox-"
NETWORK_PREFIX = "wsnet-"

# Confinement defaults (override via env)
SANDBOX_IMAGE = os.getenv(
    "WORKSPACE_SANDBOX_IMAGE",
    "ghcr.io/jmiahman1/sharedllm-workspace_runtime:latest",
)
SANDBOX_UID = int(os.getenv("WORKSPACE_SANDBOX_UID", "1000"))
SANDBOX_GID = int(os.getenv("WORKSPACE_SANDBOX_GID", "1000"))
SANDBOX_MEM = os.getenv("WORKSPACE_SANDBOX_MEM", "1g")
SANDBOX_PIDS = int(os.getenv("WORKSPACE_SANDBOX_PIDS", "256"))
SANDBOX_CPU_QUOTA = int(os.getenv("WORKSPACE_SANDBOX_CPU_QUOTA", "50000"))


def _slug(workspace_id: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (workspace_id or "").strip().lower()).strip("-")
    return s or "workspace"


def _docker_client() -> Any:
    return docker.from_env()  # type: ignore[attr-defined]


def _container_name(workspace_id: str) -> str:
    return f"{CONTAINER_PREFIX}{_slug(workspace_id)}"


def _network_name(workspace_id: str) -> str:
    return f"{NETWORK_PREFIX}{_slug(workspace_id)}"


def ensure_workspace_network(workspace_id: str) -> None:
    """Create the workspace's private bridge network (idempotent)."""
    client = _docker_client()
    net = _network_name(workspace_id)
    try:
        client.networks.get(net)
    except NotFound:
        client.networks.create(
            net,
            driver="bridge",
            internal=False,  # allow egress to internet (git push/clone, package installs)
            labels={"sharedllm": "workspace-sandbox", "workspace": workspace_id},
        )


def ensure_workspace_container(
    workspace_id: str,
    host_path: str,
    *,
    image: str | None = None,
    uid: int = SANDBOX_UID,
    gid: int = SANDBOX_GID,
) -> Any:
    """Create/start the dedicated sandbox container for ``workspace_id``.

    Only ``host_path`` is mounted (read-write) at ``/workspace``; the container
    cannot see any other workspace or host directory.
    """
    client = _docker_client()
    cname = _container_name(workspace_id)
    ensure_workspace_network(workspace_id)
    try:
        c = client.containers.get(cname)
        if c.status != "running":
            c.start()
        return c
    except NotFound:
        pass
    img = image or SANDBOX_IMAGE
    c = client.containers.run(
        img,
        name=cname,
        command=["tail", "-f", "/dev/null"],  # idle keep-alive
        detach=True,
        user=f"{uid}:{gid}",
        network=_network_name(workspace_id),
        working_dir=host_path,
        # Mount the workspace at its IDENTICAL absolute host path inside the
        # container. This keeps every absolute path an agent uses
        # (/workspaces/users/x/<id>/file.py) valid verbatim, so we never have to
        # translate paths between host and sandbox. Only this one directory is
        # visible — the agent cannot reach any other workspace or host path.
        volumes={host_path: {"bind": host_path, "mode": "rw"}},
        mem_limit=SANDBOX_MEM,
        memswap_limit=SANDBOX_MEM,
        pids_limit=SANDBOX_PIDS,
        cpu_quota=SANDBOX_CPU_QUOTA,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        read_only=False,  # /workspace is the writable volume; rootfs stays for tooling
        labels={"sharedllm": "workspace-sandbox", "workspace": workspace_id},
        restart_policy={"Name": "unless-stopped"},
        auto_remove=False,
    )
    log.info(f"[Sandbox] Created container {cname} for workspace {workspace_id} (mount {host_path})")
    return c


def remove_workspace_container(workspace_id: str) -> None:
    """Best-effort teardown of a workspace's container + network."""
    client = _docker_client()
    cname = _container_name(workspace_id)
    with contextlib.suppress(Exception):
        c = client.containers.get(cname)
        with contextlib.suppress(Exception):
            c.stop(timeout=5)
        c.remove(force=True)
    try:
        net = client.networks.get(_network_name(workspace_id))
        net.remove()
    except NotFound:
        pass


def _to_container_cwd(cwd: str | None, host_path: str) -> str:
    """The workspace is mounted at its identical absolute host path, so the
    container cwd is just the (validated) host cwd."""
    if not cwd:
        return host_path
    cwd = str(cwd)
    norm = os.path.normpath(cwd)
    norm_host = os.path.normpath(host_path)
    if norm == norm_host or norm.startswith(norm_host + os.sep):
        return norm
    # Outside the workspace: clamp into the workspace root for safety.
    return norm_host


def _build_exec_cmd(cmd: str | list[str], shell: bool) -> list[str]:
    if shell:
        if isinstance(cmd, str):
            return ["/bin/sh", "-c", cmd]
        return ["/bin/sh", "-c", " ".join(cmd)]
    if isinstance(cmd, str):
        return ["/bin/sh", "-c", cmd]
    return list(cmd)


def _exec_blocking(
    workspace_id: str,
    host_path: str,
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None,
    uid: int,
    gid: int,
    image: str | None,
) -> tuple[int, str, str]:
    client = _docker_client()
    cname = _container_name(workspace_id)
    try:
        c = client.containers.get(cname)
        if c.status != "running":
            c.start()
    except NotFound:
        c = ensure_workspace_container(workspace_id, host_path, image=image, uid=uid, gid=gid)

    full_env = dict(os.environ)
    if env:
        full_env.update({k: str(v) for k, v in env.items()})

    res = c.exec_run(
        cmd,
        user=f"{uid}:{gid}",
        workdir=cwd,
        environment=full_env,
        stdout=True,
        stderr=True,
        demux=True,
    )
    exit_code = res.exit_code
    out, err = res.output if isinstance(res.output, tuple) else (res.output, b"")
    out_s = (out or b"").decode(errors="replace")
    err_s = (err or b"").decode(errors="replace")
    return exit_code, out_s, err_s


async def run_workspace_cmd(
    workspace_id: str,
    host_path: str,
    cmd: str | list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    shell: bool = False,
    uid: int = SANDBOX_UID,
    gid: int = SANDBOX_GID,
    image: str | None = None,
) -> dict[str, Any]:
    """Run ``cmd`` inside the workspace's sandbox container.

    Returns the same shape as the existing ``_run_command`` helpers so callers
    can swap directly: ``{"args", "returncode", "stdout", "stderr"}``.
    """
    container_cwd = _to_container_cwd(cwd, host_path)
    real_cmd = _build_exec_cmd(cmd, shell)
    try:
        rc, out, err = await asyncio.wait_for(
            asyncio.to_thread(
                _exec_blocking,
                workspace_id,
                host_path,
                real_cmd,
                cwd=container_cwd,
                env=env,
                uid=uid,
                gid=gid,
                image=image,
            ),
            timeout=timeout + 5,
        )
    except TimeoutError:
        return {
            "args": real_cmd,
            "returncode": 124,
            "stdout": "",
            "stderr": f"[Sandbox] Command timed out after {timeout}s",
        }
    return {"args": real_cmd, "returncode": rc, "stdout": out, "stderr": err}
