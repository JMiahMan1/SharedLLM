"""Per-workspace sandbox execution.

Every Raven workspace gets its OWN isolated Docker container. All workspace
commands (shell, git, lint, search) execute INSIDE that container, never on
the host, with only that workspace's directory mounted, a private network, a
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
import math
import os
import re
import threading
from collections.abc import AsyncGenerator
from typing import Any

from services.common.proc import run_process

# docker is a runtime-only dependency (used for sandbox container execution),
# not needed at import time. Importing it lazily keeps modules that import this
# one (e.g. git_ops) importable in environments without the docker SDK installed
# (e.g. CI unit-test runners).
try:
    from docker.errors import NotFound

    import docker
except Exception:  # pragma: no cover - only hit when docker SDK is absent
    docker = None

    class NotFound(Exception):  # placeholder so `except NotFound` stays valid
        """Stub used only when the docker SDK is not installed."""

        pass

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
    or its .git, which breaks git status / branch operations inside the sandbox.
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
        # For a BIND mount the real source path lives in cols[3] (the "root"
        # field), not the post-dash <source> (which is the backing device,
        # e.g. /dev/nvme0n1p3). Reading the device would yield a wrong path.
        source = _unescape_mountinfo(cols[3])
        if (
            mountpoint == SANDBOX_MOUNT_ROOT
            or mountpoint.startswith(SANDBOX_MOUNT_ROOT + "/")
        ) and len(mountpoint) > len(best_mountpoint):
            best_mountpoint = mountpoint
            best_source = source
    return best_source


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
    if docker is None:
        raise RuntimeError("docker-unavailable")
    return docker.from_env(timeout=5)  # type: ignore[attr-defined]


_thread_clients = threading.local()


def _get_client() -> Any:
    """Return a Docker client, or None when no daemon is reachable.

    A short timeout means environments without Docker (CI runners, dev
    machines) fail fast and fall back to host execution instead of hanging.
    One client per thread is reused: a new one per command was never closed,
    leaving its connection pool to the garbage collector.
    """
    cached = getattr(_thread_clients, "client", None)
    if cached is not None:
        return cached
    try:
        _thread_clients.client = _docker_client()
        return _thread_clients.client
    except Exception as e:  # DockerException / requests errors when no daemon
        log.warning(f"[Sandbox] Docker daemon unavailable, sandbox disabled: {e}")
        return None


def _container_name(workspace_id: str) -> str:
    return f"{CONTAINER_PREFIX}{_slug(workspace_id)}"


def _current_image_id(client: Any, img: str) -> str | None:
    """Local image ID for ``img``, or None when it cannot be resolved.

    None is treated as 'unknown' and callers skip recreation, so a sandbox is
    never torn down just because the image cannot be inspected (e.g. not yet
    pulled on first run)."""
    try:
        return client.images.get(img).id
    except Exception:
        return None


def _mounts_path(container: Any, host_path: str) -> bool | None:
    """Whether ``container`` bind-mounts ``host_path`` (None when unknown)."""
    mounts = (getattr(container, "attrs", None) or {}).get("Mounts")
    if not isinstance(mounts, list):
        return None
    want = os.path.normpath(host_path)
    return any(os.path.normpath(str(m.get("Destination") or "")) == want for m in mounts if isinstance(m, dict))


def _get_or_recreate(client: Any, cname: str, img: str, host_path: str | None = None) -> Any | None:
    """Return the running container for ``cname``, recreating it when its image
    is stale.

    Deploys rebuild ``ghcr.io/...:latest`` and ``docker compose up -d`` only
    recreates the *service* containers; existing ``wsbox-*`` sandboxes keep
    their original image indefinitely. The toolchain probe advertises tools
    from the fresh image, so a stale sandbox then fails with ``command not
    found`` (e.g. ``pdflatex`` after a texlive deploy) even though the tool is
    truthfully listed. Recreating on image change keeps the shell in sync with
    what the probe advertises.

    Returns None when the container does not exist or was removed (the caller
    creates it from ``img``); otherwise the (possibly just recreated) container.
    """
    try:
        c = client.containers.get(cname)
    except NotFound:
        return None
    # A sandbox is found by name, so one made for another path (the
    # workspace's local_path changed, or two ids slug to the same name) would
    # be reused with the workspace not mounted, and every command would run
    # in a directory that is not there. Recreate it for the right path.
    if host_path and _mounts_path(c, host_path) is False:
        log.info(f"[Sandbox] {cname} does not mount {host_path}; recreating it")
        try:
            c.remove(force=True)
            return None
        except Exception as e:
            log.warning(f"[Sandbox] Could not recreate {cname} for its new path: {e}")
            return c
    current_id = _current_image_id(client, img)
    if current_id is not None and getattr(c.image, "id", None) != current_id:
        log.info(f"[Sandbox] Image changed for {cname}; recreating from {img}")
        try:
            c.remove(force=True)
        except Exception as e:
            log.warning(
                f"[Sandbox] Could not recreate {cname} (image changed), reusing stale container: {e}"
            )
            return c
        return None
    return c


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


# Integration credentials the sandbox must inherit so `gh`/`git`/etc. inside the
# container can authenticate as the owning user. Sourced from the execution
# service's environment (which holds the resolved integration tokens) and passed
# into every sandbox container at creation time. Without these, git push / gh
# Integration credentials the sandbox must inherit so `gh`/`git`/etc. inside the
# container can authenticate as the owning user. Sourced from the execution
# service's environment (which holds the resolved integration tokens) and passed
# into every sandbox container at creation time. Without these, git push / gh
# commands inside the sandbox fail with rc=128 / "not logged in".
_SANDBOX_CRED_ENV_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GIT_TOKEN",
    "GITLAB_TOKEN",
    "NEXTCLOUD_PASSWORD",
    "NEXTCLOUD_PASS",
    "NEXTCLOUD_URL",
    "HA_TOKEN",
    "HOME_ASSISTANT_TOKEN",
    "HA_URL",
)

# Non-secret environment keys copied into sandbox execs. The sandbox runs
# arbitrary repo code, so the service's full environment (INTERNAL_SECRET,
# FERNET_KEY, cloud keys, ...) must never be passed through wholesale;
# only this baseline plus the scoped _SANDBOX_CRED_ENV_KEYS overlay.
#
# HOME is deliberately NOT here: it is set per exec to the *sandbox user's* real
# home (see _home_for_uid). A caller used to pass HOME=/home/sharedllm, which
# exists only in the gateway image -- inside the sandbox (the workspace_runtime
# image, whose user is `raven` in /home/raven) it does not exist and uid 1000
# cannot create it under /home, so npm, `pip install --user`, `git config`
# (without GIT_CONFIG_*), cargo's registry and bash's own startup all failed
# with EACCES.
_SANDBOX_BASE_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "TMPDIR",
    "TERM",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)

#: Fallback home when the sandbox user has no passwd entry (always writable).
_SANDBOX_FALLBACK_HOME = "/tmp"

#: uid -> home, per container id. A container's user does not change under us.
_HOME_CACHE: dict[tuple[str, int], str] = {}


#: Home Assistant's keys: passed to a sandbox only while that integration is on.
_SANDBOX_HA_ENV_KEYS = frozenset({"HA_TOKEN", "HOME_ASSISTANT_TOKEN", "HA_URL"})


def _home_assistant_on() -> bool:
    from services import config

    return config.home_assistant_enabled()


def _sandbox_credential_env() -> dict[str, str]:
    """Collect integration credentials from the host env to inject into sandboxes.

    No Home Assistant login or address while that integration is off. While it is on,
    the address is the one resolved at startup (``services.config.HA_URL``, redirected
    away from a retired host), not the raw environment.
    """
    env = {
        k: os.environ[k]
        for k in _SANDBOX_CRED_ENV_KEYS
        if k not in _SANDBOX_HA_ENV_KEYS and os.environ.get(k)
    }
    if _home_assistant_on():
        from services import config

        for k in ("HA_TOKEN", "HOME_ASSISTANT_TOKEN"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        if config.HA_URL and ("HA_TOKEN" in env or "HOME_ASSISTANT_TOKEN" in env):
            env["HA_URL"] = str(config.HA_URL)
    return env


def _sandbox_credential_env_with_user(user_context: dict[str, Any]) -> dict[str, str]:
    """Collect integration credentials from the host env and merge with user context."""
    # Start with host environment credentials
    cred_env = _sandbox_credential_env()
    # Overlay with user context if present (Identity service already decrypted)
    if isinstance(user_context, dict):
        keys = ["GH_TOKEN", "GITHUB_TOKEN", "GIT_TOKEN", "GITLAB_TOKEN"]
        if _home_assistant_on():
            keys.append("HA_TOKEN")
        for key in keys:
            if user_context.get(key):
                cred_env[key] = str(user_context[key])
    return cred_env


def ensure_workspace_container(
    workspace_id: str,
    host_path: str,
    *,
    image: str | None = None,
    uid: int = SANDBOX_UID,
    gid: int = SANDBOX_GID,
    env: dict[str, str] | None = None,
) -> Any:
    """Create/start the dedicated sandbox container for ``workspace_id``.

    Only ``host_path`` is mounted (read-write) at ``/workspace``; the container
    cannot see any other workspace or host directory.

    The container inherits the user's integration credentials (GitHub token,
    etc.) from ``env`` (or, when not supplied, from the execution service's
    environment) so that `gh`/`git push` and other integration CLIs inside the
    sandbox can authenticate.
    """
    client = _get_client()
    if client is None:
        return None
    cname = _container_name(workspace_id)
    img = image or SANDBOX_IMAGE
    ensure_workspace_network(workspace_id)
    c = _get_or_recreate(client, cname, img, host_path)
    if c is not None:
        if c.status != "running":
            c.start()
        return c
    # Merge inherited integration credentials so the sandbox can authenticate.
    cred_env = _sandbox_credential_env()
    if env:
        cred_env.update({k: str(v) for k, v in env.items()})
    # The container's own HOME: an interactive terminal (and any exec that names
    # no environment) inherits it, so bash does not try to read /root/.bashrc as
    # a non-root user. It is set from the image's own passwd entry, never guessed.
    container_home = _image_home_for_uid(img, uid, client) or _SANDBOX_FALLBACK_HOME
    cred_env.setdefault("HOME", container_home)
    # The SOURCE must be the real Docker-host path (e.g. /home/<user>/workspaces/...),
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
        environment=cred_env or None,
        # Mount the workspace at its IDENTICAL absolute path inside the container.
        # Only this directory is visible; the agent cannot reach any other
        # workspace or host path. The trailing ",z" applies the shared SELinux
        # relabel so a non-privileged sandbox container (container_t) can read
        # the host workspace when the host enforces SELinux. Without it, ls/git
        # inside the sandbox fail with "Permission denied" even as root because
        # the source lives under /home (user_home_t). Ignored on non-SELinux hosts.
        volumes={mount_source: {"bind": host_path, "mode": "rw,z"}},
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
    _configure_git_credentials(c)
    return c





def _configure_git_credentials(container: Any) -> None:
    """Install a git credential helper that authenticates with the inherited
    ``GH_TOKEN``/``GITHUB_TOKEN`` so raw ``git push``/``git ls-remote`` over HTTPS
    work inside the sandbox (not just the ``gh`` CLI). Without this, ``git`` over
    HTTPS cannot read the token from ``GH_TOKEN`` and fails with
    "could not read Username".

    A system-wide ``/etc/gitconfig`` is used (not ``~/.gitconfig``) because the
    sandbox user's HOME may be unset, and ``/etc/gitconfig`` is honoured
    regardless of HOME. Files are written via an in-container Python one-liner to
    avoid shell-heredoc quoting pitfalls inside ``docker exec``.
    """
    helper = (
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  get)\n"
        "    tok=\"${GH_TOKEN:-${GITHUB_TOKEN:-}}\"\n"
        "    if [ -n \"$tok\" ]; then\n"
        "      echo protocol=https\n"
        "      echo host=github.com\n"
        "      echo username=token\n"
        "      echo password=$tok\n"
        "    fi\n"
        "    ;;\n"
        "esac\n"
    )
    gitconfig = (
        "[credential \"https://github.com\"]\n"
        "  helper = /usr/local/bin/git-credential-gh-token\n"
    )
    writer = (
        "import os;"
        "os.makedirs('/usr/local/bin', exist_ok=True);"
        f"open('/usr/local/bin/git-credential-gh-token','w').write({helper!r});"
        "os.chmod('/usr/local/bin/git-credential-gh-token', 0o755);"
        f"open('/etc/gitconfig','w').write({gitconfig!r})"
    )
    with contextlib.suppress(Exception):
        container.exec_run(["python3", "-c", writer], user="0:0")


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
    # Any failure here (an endpoint still attached is an APIError, not
    # NotFound) used to turn DELETE /workspaces/{id} into a 500 after the
    # workspace row was already gone. Teardown is best-effort.
    try:
        net = client.networks.get(_network_name(workspace_id))
        net.remove()
    except NotFound:
        pass
    except Exception as e:
        log.warning(f"[Sandbox] Could not remove the network of {workspace_id}: {e}")


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


def _sandbox_exec_env(env: dict[str, str] | None) -> dict[str, str]:
    """The environment a workspace command runs with: the non-secret baseline,
    the integration credentials (so `gh` and git pushes authenticate even when
    the caller passed none) and the caller's own variables."""
    full_env = {k: os.environ[k] for k in _SANDBOX_BASE_ENV_KEYS if k in os.environ}
    for _k, _v in _sandbox_credential_env().items():
        full_env.setdefault(_k, _v)
    if env:
        full_env.update({k: str(v) for k, v in env.items()})
    return full_env


def _image_home_for_uid(image: str, uid: int, client: Any) -> str:
    """The uid's home as the *image* defines it, read once and cached.

    Asked of a throwaway container so a workspace sandbox can be created with the
    right ``HOME`` before the workspace container exists. Failure is not fatal:
    the caller falls back to ``/tmp``.
    """
    key = (f"image:{image}", int(uid))
    cached = _HOME_CACHE.get(key)
    if cached:
        return cached
    home = ""
    try:
        probe = client.containers.run(
            image,
            command=["sh", "-c", f"getent passwd {int(uid)} | cut -d: -f6"],
            remove=True,
            user="0:0",
            network_disabled=True,
        )
        candidate = (probe or b"").decode(errors="replace").strip()
        if candidate.startswith("/"):
            home = candidate
    except Exception as exc:  # image not pulled, no getent, docker hiccup
        log.debug("[Sandbox] could not read a home for uid %s from %s: %s", uid, image, exc)
    if home:
        _HOME_CACHE[key] = home
    return home


def _home_for_uid(container: Any, uid: int) -> str:
    """The sandbox user's home directory, as the container itself reports it.

    Tools write to ``$HOME`` (npm's cache, ``pip --user``, git's config, bash's
    history). If it points somewhere the user cannot write, every one of them
    fails with EACCES and the workspace looks broken. The container's own passwd
    entry is the only trustworthy answer across images; ``/tmp`` is the fallback
    because it is world-writable.

    One exec per (container, uid): the answer cannot change while the container
    lives, and a lookup per command would double the round trips.
    """
    key = (getattr(container, "id", "") or getattr(container, "name", ""), int(uid))
    cached = _HOME_CACHE.get(key)
    if cached:
        return cached
    home = ""
    try:
        probe = container.exec_run(
            ["sh", "-c", f"getent passwd {int(uid)} | cut -d: -f6"],
            user="0:0",
            stdout=True,
            stderr=False,
        )
        out = probe.output if isinstance(probe.output, bytes) else b""
        candidate = out.decode(errors="replace").strip()
        if candidate.startswith("/"):
            home = candidate
    except Exception as exc:  # no getent, no permission, docker hiccup
        log.debug("[Sandbox] could not read the home for uid %s: %s", uid, exc)
    if not home:
        home = _SANDBOX_FALLBACK_HOME
    _HOME_CACHE[key] = home
    return home


def build_exec_env(
    *,
    home: str,
    cred_env: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """The environment one sandbox exec gets.

    Only the documented baseline plus credentials plus the caller's own keys.
    ``HOME`` is always the last word: a caller that names a path the sandbox user
    cannot write (``/home/sharedllm`` in a ``raven``-based image) is exactly the
    bug this prevents.
    """
    env = {k: os.environ[k] for k in _SANDBOX_BASE_ENV_KEYS if k in os.environ}
    for key, value in (cred_env or {}).items():
        env.setdefault(str(key), str(value))
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    env["HOME"] = home or _SANDBOX_FALLBACK_HOME
    return env


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
    c = _get_or_recreate(client, cname, img, host_path)
    if c is None:
        # Only create the sandbox if its image is already pulled locally.
        # Pulling a large image mid-command would hang; in that case we raise
        # so run_workspace_cmd falls back to a host subprocess (CI runners and
        # first-run hosts without a pre-pulled image). In the real deployment
        # the image is always present on the host, so creation is instant.
        try:
            client.images.get(img)
        except NotFound:
            raise RuntimeError("sandbox-image-not-present") from None
        c = ensure_workspace_container(workspace_id, host_path, image=image, uid=uid, gid=gid)
        if c is None:
            raise RuntimeError("docker-unavailable") from None
    elif c.status != "running":
        c.start()

    full_env = build_exec_env(
        home=_home_for_uid(c, uid),
        cred_env=_sandbox_credential_env(),
        extra=env,
    )

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
    """Run a command for a workspace that lives outside the sandbox mount root
    (tests and dev machines) as a host subprocess.

    The environment is the same one a sandbox exec gets. It used to be the
    service's whole environment, which handed INTERNAL_SECRET, FERNET_KEY and
    every other service secret to whatever the command was.
    """
    run_env = _sandbox_exec_env(env)
    try:
        rc, out, err = await run_process(cmd, cwd=cwd, env=run_env, shell=shell, timeout=timeout)
        if rc is None:
            return {"args": cmd, "returncode": 124, "stdout": "", "stderr": f"[host] Command timed out after {timeout}s"}
        return {"args": cmd, "returncode": rc, "stdout": out, "stderr": err}
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
        # container_cwd, not cwd: the host fallback has to land in the workspace
        # too. Passing cwd through (None for most callers) ran the command in
        # the service's own working directory, so `ls` listed the service root,
        # pytest collected the wrong tree, git reported the wrong repo, and a
        # relative write landed outside the workspace. It is also the clamped
        # value, so an escape attempt is contained on the host as well.
        return await _host_exec(cmd, cwd=container_cwd, env=env, shell=shell, timeout=timeout)
    # The time limit is enforced inside the container. wait_for alone only
    # stopped waiting: exec_run has no timeout, so a command that never exits
    # (a dev server, tail -f, a prompt) kept its thread and process for good,
    # and enough of them used up the thread pool the whole service shares.
    limited = ["timeout", "-k", "5", str(max(1, math.ceil(timeout))), *real_cmd]
    try:
        rc, out, err = await asyncio.wait_for(
            asyncio.to_thread(
                _exec_blocking,
                workspace_id,
                host_path,
                limited,
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
        # A workspace under the mount root runs in its sandbox or not at all.
        # This used to run the command in the service's own container instead,
        # silently, outside the sandbox the agent's code is meant to be in.
        log.error(f"[Sandbox] docker execution failed for {workspace_id}; not running it outside the sandbox: {e}")
        return {
            "args": real_cmd,
            "returncode": 125,
            "stdout": "",
            "stderr": f"[Sandbox] The workspace sandbox is unavailable ({type(e).__name__}: {e}). The command did not run.",
        }
    if rc == 124:
        err = (err + "\n" if err else "") + f"[Sandbox] Command timed out after {timeout}s"
    return {"args": real_cmd, "returncode": rc, "stdout": out, "stderr": err}


async def run_workspace_terminal(
    workspace_id: str,
    host_path: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    uid: int = SANDBOX_UID,
    gid: int = SANDBOX_GID,
    image: str | None = None,
    width: int = 80,
    height: int = 24,
) -> AsyncGenerator[tuple[bytes, bytes], None]:
    """Run an interactive terminal PTY inside the workspace's sandbox container.

    Yields tuples of (stdout_data, stderr_data) as they become available.
    The caller is responsible for sending stdin data via the returned stdin
    queue and handling terminal resize via the resize_pty function.

    This is a low-level primitive - callers must manage the PTY lifecycle.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("docker-unavailable")
    cname = _container_name(workspace_id)
    img = image or SANDBOX_IMAGE
    c = _get_or_recreate(client, cname, img, host_path)
    if c is None:
        # Only create the sandbox if its image is already pulled locally.
        try:
            client.images.get(img)
        except NotFound:
            raise RuntimeError("sandbox-image-not-present") from None
        c = ensure_workspace_container(workspace_id, host_path, image=image, uid=uid, gid=gid)
        if c is None:
            raise RuntimeError("docker-unavailable") from None
    elif c.status != "running":
        c.start()

    # Build environment with credentials: the documented baseline plus creds,
    # never the service's whole environment (see _SANDBOX_BASE_ENV_KEYS), and a
    # HOME the sandbox user can actually write to.
    full_env = build_exec_env(
        home=_home_for_uid(c, uid),
        cred_env=_sandbox_credential_env(),
        extra=env,
    )

    # Create the PTY
    # Using docker-py's low-level API to get direct socket access
    exec_id = None
    try:
        # Create the exec instance with a PTY
        exec_resp = client.api.exec_create(
            c.id,
            cmd=["/bin/sh"],
            user=f"{uid}:{gid}",
            workdir=_to_container_cwd(cwd, host_path) if cwd else host_path,
            environment=full_env,
            tty=True,  # Allocate a pseudo-TTY
            stdin=True,  # Keep stdin open
            stdout=True,
            stderr=True,
            demux=False,  # We'll handle multiplexing ourselves
        )
        exec_id = exec_resp["Id"]

        # Start the exec process with a socket
        sock = client.api.exec_start(exec_id, detach=False, tty=True, socket=True)

        # Set terminal size
        client.api.exec_resize(exec_id, height=height, width=width)

        # Convert socket to asyncio-friendly streams
        # We'll use a simple approach: read from socket in a thread and yield chunks

        def _socket_reader():
            """Read from the socket in a blocking thread."""
            try:
                while True:
                    data = sock.recv(4096)
                    if not data:
                        break
                    yield data
            except Exception as e:
                log.warning(f"[Sandbox terminal] Socket read error: {e}")
            finally:
                sock.close()

        # For simplicity in this implementation, we'll use a blocking approach
        # and yield data as it becomes available. In production, this would
        # use proper asyncio socket handling.
        while True:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                # Split stdout/stderr - in demux=False mode, we get mixed output
                # For a real implementation, we'd need to parse the TTY output
                # or use demux=True and handle the separation properly
                yield (data, b"")  # For now, treat all as stdout
            except Exception as e:
                log.warning(f"[Sandbox terminal] Error reading from socket: {e}")
                break

    finally:
        # Clean up the exec instance if it was created
        if exec_id:
            # Ignore cleanup errors
            with contextlib.suppress(Exception):
                client.api.exec_inspect(exec_id)  # Check if it exists
                # Note: We don't kill the exec here as it should exit naturally
                # when the shell exits. The container remains running.


def resize_workspace_terminal(
    workspace_id: str,
    width: int,
    height: int,
) -> bool:
    """Resize an active terminal PTY in the workspace's sandbox container.

    Returns True if successful, False otherwise.
    """
    client = _get_client()
    if client is None:
        return False
    cname = _container_name(workspace_id)
    try:
        client.containers.get(cname)
        # Find the most recent exec instance for this container
        # In a real implementation, we'd track the exec ID
        # For now, we'll rely on the fact that there's typically one
        # active terminal per workspace
        execs = client.api.exec_list()
        for exec_info in execs:
            if exec_info.get("Running", False) and exec_info.get("ID", "").startswith():
                client.api.exec_resize(exec_info["Id"], height=height, width=width)
                return True
    except Exception as e:
        log.warning(f"[Sandbox terminal] Failed to resize PTY: {e}")
    return False


# ---------------------------------------------------------------------------
# Host Port Exposure & TCP Proxy Forwarding for Sandbox Workspaces
# ---------------------------------------------------------------------------
_PORT_FORWARD_LOCK = threading.Lock()
_ACTIVE_PORT_FORWARDS: dict[tuple[str, int], tuple[threading.Thread, threading.Event, int, str]] = {}


def _find_free_host_port(start_port: int = 9000, max_attempts: int = 200) -> int:
    import socket
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free host ports available in specified range")


def _run_tcp_proxy(host_port: int, target_ip: str, target_port: int, stop_event: threading.Event) -> None:
    import socket

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.settimeout(1.0)
    try:
        server_sock.bind(("0.0.0.0", host_port))
        server_sock.listen(128)
    except Exception as e:
        log.error(f"[Sandbox Port Forward] Failed to bind host port {host_port}: {e}")
        return

    log.info(f"[Sandbox Port Forward] Forwarding 0.0.0.0:{host_port} -> {target_ip}:{target_port}")

    def handle_client(client_sock: socket.socket) -> None:
        try:
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.settimeout(10.0)
            target_sock.connect((target_ip, target_port))
            target_sock.settimeout(None)
            client_sock.settimeout(None)
        except Exception as err:
            log.warning(f"[Sandbox Port Forward] Cannot connect to {target_ip}:{target_port}: {err}")
            client_sock.close()
            return

        def pipe(src: socket.socket, dst: socket.socket) -> None:
            try:
                while not stop_event.is_set():
                    data = src.recv(8192)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    src.shutdown(socket.SHUT_RDWR)
                with contextlib.suppress(Exception):
                    dst.shutdown(socket.SHUT_RDWR)

        t1 = threading.Thread(target=pipe, args=(client_sock, target_sock), daemon=True)
        t2 = threading.Thread(target=pipe, args=(target_sock, client_sock), daemon=True)
        t1.start()
        t2.start()

    while not stop_event.is_set():
        try:
            client_sock, _ = server_sock.accept()
            t = threading.Thread(target=handle_client, args=(client_sock,), daemon=True)
            t.start()
        except TimeoutError:
            continue
        except Exception as e:
            if not stop_event.is_set():
                log.warning(f"[Sandbox Port Forward] Accept error on port {host_port}: {e}")
            break

    with contextlib.suppress(Exception):
        server_sock.close()
    log.info(f"[Sandbox Port Forward] Stopped forwarding on host port {host_port}")


def _forward_host() -> str:
    """The LAN address devices use to reach a forwarded workspace port.

    This used to be hardcoded to one machine's LAN address, so on
    any other host the URL handed to the UI pointed somewhere that does not
    exist. EXECUTION_EXTERNAL_HOST is already the configured answer -- the
    execution service builds its stream URLs from the same value -- and when it
    is unset we say so instead of guessing.
    """
    host = (os.environ.get("EXECUTION_EXTERNAL_HOST") or "").strip()
    if not host:
        raise RuntimeError(
            "EXECUTION_EXTERNAL_HOST is not set, so a workspace port cannot be "
            "given a reachable URL. Set it to this host's LAN address (see "
            ".env.example) and retry."
        )
    return host


def _host_forward_url(port: int) -> str:
    """The LAN URL for a forwarded workspace port."""
    return f"http://{_forward_host()}:{port}"


def expose_workspace_port(
    workspace_id: str,
    container_port: int,
    host_port: int | None = None,
) -> dict[str, Any]:
    """Expose a container port running inside a sandbox workspace to the host IP."""
    # Resolve the host first: raising after the forward thread is running would
    # leave a listener nobody can reach.
    host = _forward_host()
    client = _get_client()
    cname = _container_name(workspace_id)
    net_name = _network_name(workspace_id)

    container_ip = None
    if client:
        try:
            c = client.containers.get(cname)
            networks = c.attrs.get("NetworkSettings", {}).get("Networks", {})
            if net_name in networks:
                container_ip = networks[net_name].get("IPAddress")
            if not container_ip:
                for n_info in networks.values():
                    if n_info.get("IPAddress"):
                        container_ip = n_info.get("IPAddress")
                        break
        except Exception as e:
            log.warning(f"[Sandbox Port Forward] Could not inspect IP for {workspace_id}: {e}")

    if not container_ip:
        container_ip = "127.0.0.1"

    with _PORT_FORWARD_LOCK:
        if not host_port:
            host_port = _find_free_host_port(9000)

        key = (workspace_id, host_port)
        if key in _ACTIVE_PORT_FORWARDS:
            _, old_evt, _, _ = _ACTIVE_PORT_FORWARDS[key]
            old_evt.set()
            del _ACTIVE_PORT_FORWARDS[key]

        stop_evt = threading.Event()
        thread = threading.Thread(
            target=_run_tcp_proxy,
            args=(host_port, container_ip, container_port, stop_evt),
            daemon=True,
        )
        thread.start()
        _ACTIVE_PORT_FORWARDS[key] = (thread, stop_evt, container_port, container_ip)

    return {
        "workspace_id": workspace_id,
        "container_port": container_port,
        "container_ip": container_ip,
        "host_port": host_port,
        "status": "active",
        "url": f"http://{host}:{host_port}",
    }


def unexpose_workspace_port(workspace_id: str, host_port: int) -> bool:
    with _PORT_FORWARD_LOCK:
        key = (workspace_id, host_port)
        if key in _ACTIVE_PORT_FORWARDS:
            _, stop_evt, _, _ = _ACTIVE_PORT_FORWARDS[key]
            stop_evt.set()
            del _ACTIVE_PORT_FORWARDS[key]
            return True
    return False


def unexpose_all_workspace_ports(workspace_id: str) -> int:
    """Stop every port forward of a workspace (when it is deleted); returns how many.

    Nothing stopped them before, so each expose kept a listener thread and a
    host port from the 9000-9199 range until the service restarted.
    """
    with _PORT_FORWARD_LOCK:
        keys = [k for k in _ACTIVE_PORT_FORWARDS if k[0] == workspace_id]
        for key in keys:
            _ACTIVE_PORT_FORWARDS.pop(key)[1].set()
    return len(keys)


def list_workspace_ports(workspace_id: str) -> list[dict[str, Any]]:
    result = []
    with _PORT_FORWARD_LOCK:
        for (ws_id, h_port), (_, _, c_port, c_ip) in _ACTIVE_PORT_FORWARDS.items():
            if ws_id == workspace_id:
                result.append({
                    "workspace_id": ws_id,
                    "container_port": c_port,
                    "container_ip": c_ip,
                    "host_port": h_port,
                    "url": _host_forward_url(h_port),
                })
    return result
