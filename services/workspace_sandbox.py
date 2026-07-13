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
SANDBOX_MOUNT_ROOT = os.getenv("SANDBOX_MOUNT_ROOT", "/workspaces")
# The Docker host path that the in-container SANDBOX_MOUNT_ROOT (e.g. /workspaces)
# is itself a bind-mount of. The Docker daemon interprets bind-mount SOURCES as
# paths on the Docker HOST filesystem, not inside the calling container, so we
# must translate /workspaces/... -> $WORKSPACE_HOST_PATH/... for the mount.
SANDBOX_HOST_ROOT = os.getenv("WORKSPACE_HOST_PATH", "")


def _unescape_mountinfo(field: str) -> str:
    # /proc/self/mountinfo encodes non-printables (e.g. spaces) as octal \NNN.
    return re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1), 8)), field)


def _detect_host_root() -> str:
    """Best-effort detection of the Docker-host path backing SANDBOX_MOUNT_ROOT.

    Used when WORKSPACE_HOST_PATH is unset/empty. The Docker daemon resolves
    bind-mount SOURCES against the host filesystem, so we read
    /proc/self/mountinfo to find the real host source of the /workspaces bind
    mount instead of the container-internal path. Without this the sandbox binds
    an empty, auto-created host directory and cannot see the workspace's files
    or its .git — which breaks git status / branch operations inside the sandbox.
    """
    try:
        with open("/proc/self/mountinfo") as fh:
            rows = fh.read().splitlines()
    except OSError:
        return ""
    best_mountpoint = ""
    best_source = ""
    for row in rows:
        cols = row.split()
        if len(cols) < 7 or "-" not in cols:
            continue
        mountpoint = _unescape_mountinfo(cols[4])
        sep = cols.index("-")
        if sep + 2 >= len(cols):
            continue
        # mountinfo: ... <options> - <fstype> <source> <super-options>
        source = _unescape_mountinfo(cols[sep + 2])
        if mountpoint == SANDBOX_MOUNT_ROOT or mountpoint.startswith(
            SANDBOX_MOUNT_ROOT + "/"
        ):
            if len(mountpoint) > len(best_mountpoint):
                best_mountpoint = mountpoint
                best_source = source
    return best_source


# Resolve the host root: prefer the explicit override, otherwise detect the real
# host source of the /workspaces bind mount so the sandbox sees actual data
# (including .git) rather than an empty auto-created directory.
if not SANDBOX_HOST_ROOT:
    _detected = _detect_host_root()
    if _detected:
        SANDBOX_HOST_ROOT = _detected
        log.info(
            f"[Sandbox] WORKSPACE_HOST_PATH unset; detected host root "
            f"{SANDBOX_HOST_ROOT} from mountinfo"
        )


def _under_mount_root(path: str) -> bool:
    """True when `path` lies within the sandbox mount root.

    Workspaces outside the mount root cannot be safely bind-mounted into the
    sandbox container, so callers fall back to host execution instead.
    """
    try:
        return (
            os.path.commonpath([os.path.abspath(path), SANDBOX_MOUNT_ROOT])
            == SANDBOX_MOUNT_ROOT
        )
    except ValueError:
        return False


def _host_mount_source(container_path: str) -> str:
    """Translate an in-container /workspaces path to the real Docker-host path.

    The daemon mounts the SOURCE from the host filesystem. If we passed the
    container-internal "/workspaces/..." path as the source, the daemon would
    bind an unrelated (often empty, auto-created) host directory instead of the
    real workspace data, so the sandbox could neither see nor write the
    workspace. When WORKSPACE_HOST_PATH is set we map /workspaces/<rest> to
    <WORKSPACE_HOST_PATH>/<rest>; otherwise the path is used as-is (tests,
    dev boxes where /workspaces is already a real host path).
    """
    if SANDBOX_HOST_ROOT and container_path.startswith(SANDBOX_MOUNT_ROOT + "/"):
        rel = os.path.relpath(container_path, SANDBOX_MOUNT_ROOT)
        return os.path.join(SANDBOX_HOST_ROOT, rel)
    return container_path


def _slug(workspace_id: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (workspace_id or "").strip().lower()).strip("-")
    return s or "workspace"


def _docker_client() -> Any:
    return docker.from_env(timeout=5)  # type: ignore[attr-defined]


def _get_client() -> Any:
    """Return a Docker client, or None when no daemon is reachable.

    A short timeout means environments without Docker (CI runners, dev
    machines) fail fast and fall back to host execution instead of hanging.
    """
    try:
        return _docker_client()
    except Exception as e:  # DockerException / requests errors when no daemon
        log.warning(f"[Sandbox] Docker daemon unavailable, sandbox disabled: {e}")
        return None


def _container_name(workspace_id: str) -> str:
    return f"{CONTAINER_PREFIX}{_slug(workspace_id)}"


def _network_name(workspace_id: str) -> str:
    return f"{NETWORK_PREFIX}{_slug(workspace_id)}"


def ensure_workspace_network(workspace_id: str) -> None:
    """Create the workspace's private bridge network (idempotent)."""
    client = _get_client()
    if client is None:
        return
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
    client = _get_client()
    if client is None:
        return None
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
    # The SOURCE must be the real Docker-host path (e.g. /home/jeremiah/workspaces/...),
    # not the container-internal /workspaces/... path, or the daemon would bind an
    # unrelated empty host directory. The TARGET stays host_path so the agent's
    # absolute paths remain valid verbatim inside the container.
    mount_source = _host_mount_source(host_path)
    c = client.containers.run(
        img,
        name=cname,
        command=["tail", "-f", "/dev/null"],  # idle keep-alive
        detach=True,
        user=f"{uid}:{gid}",
        network=_network_name(workspace_id),
        working_dir=host_path,
        # Mount the workspace at its IDENTICAL absolute path inside the container.
        # Only this directory is visible — the agent cannot reach any other
        # workspace or host path.
        volumes={mount_source: {"bind": host_path, "mode": "rw"}},
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
    log.info(f"[Sandbox] Created container {cname} for workspace {workspace_id} (mount {mount_source} -> {host_path})")
    return c


def remove_workspace_container(workspace_id: str) -> None:
    """Best-effort teardown of a workspace's container + network."""
    client = _get_client()
    if client is None:
        return
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
    client = _get_client()
    if client is None:
        raise RuntimeError("docker-unavailable")
    cname = _container_name(workspace_id)
    img = image or SANDBOX_IMAGE
    try:
        c = client.containers.get(cname)
        if c.status != "running":
            c.start()
    except NotFound:
        # Only create the sandbox if its image is already pulled locally.
        # Pulling a large image mid-command would hang; in that case we raise
        # so run_workspace_cmd falls back to a host subprocess (CI runners and
        # first-run hosts without a pre-pulled image). In the real deployment
        # the image is always present on the host, so creation is instant.
        try:
            client.images.get(img)
        except NotFound:
            raise RuntimeError("sandbox-image-not-present")
        c = ensure_workspace_container(workspace_id, host_path, image=image, uid=uid, gid=gid)
        if c is None:
            raise RuntimeError("docker-unavailable")

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


async def _host_exec(
    cmd: str | list[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    shell: bool,
    timeout: float,
) -> dict[str, Any]:
    """Fallback used when the Docker daemon is unavailable: run the command on
    the host exactly like the legacy path, so callers keep working in CI / on
    dev machines without Docker."""
    run_env = dict(os.environ)
    if env:
        run_env.update({k: str(v) for k, v in env.items()})
    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                cmd if isinstance(cmd, str) else " ".join(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=run_env,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=run_env,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            rc = proc.returncode if proc.returncode is not None else 0
            return {"args": cmd, "returncode": rc, "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")}
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return {"args": cmd, "returncode": 124, "stdout": "", "stderr": f"[host] Command timed out after {timeout}s"}
    except FileNotFoundError as e:
        return {"args": cmd, "returncode": -1, "stdout": "", "stderr": f"Executable or directory not found: {e}"}


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
    # Workspaces outside the sandbox mount root can't be bind-mounted into the
    # container, so run on the host instead (also covers test/dev paths).
    if not host_path or not _under_mount_root(host_path):
        log.info(f"[Sandbox] {workspace_id} root {host_path} not under {SANDBOX_MOUNT_ROOT}; using host exec")
        return await _host_exec(cmd, cwd=cwd, env=env, shell=shell, timeout=timeout)
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
    except Exception as e:
        # Docker daemon unreachable or exec failed: fall back to a host
        # subprocess so the command still runs (CI runners, dev machines).
        log.warning(f"[Sandbox] docker execution failed for {workspace_id}, falling back to host: {e}")
        return await _host_exec(
            cmd,
            cwd=cwd,
            env=env,
            shell=shell,
            timeout=timeout,
        )
    return {"args": real_cmd, "returncode": rc, "stdout": out, "stderr": err}
