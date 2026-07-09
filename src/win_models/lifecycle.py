"""Process lifecycle management for win-models.

Replaces the ad-hoc ``subprocess.Popen`` + ``taskkill`` / ``netstat`` /
``tasklist`` shell-outs scattered across ``edge.py``, ``unsloth.py``,
``comfyui.py``, ``parakeet_asr.py``, ``plain_llama.py``, and ``utils.py``
with a single set of primitives built on **psutil**.

Design:

* ``pids_on_port(port)`` — replaces the netstat text parser and the four
  PowerShell ``Get-NetTCPConnection`` call sites.
* ``kill_pid(pid, *, graceful, tree, timeout)`` — replaces the
  ``taskkill /F /T /PID`` boilerplate.  Always tries graceful first
  (``proc.terminate()``), waits, then force-kills (``proc.kill()``).
  Tree-kill walks ``proc.children(recursive=True)``.
* ``stop_on_port(port, ...)`` / ``stop_by_name(name, ...)`` — thin
  convenience wrappers.
* ``wait_for_url(url, timeout, predicate)`` — generalises
  ``common.wait_openai_server`` to any HTTP endpoint with a custom
  predicate (``/models``, ``/health``, raw 200, ...).
* ``PidFile`` — context manager for writing/reading/clearing PID files
  so that ``stop`` can kill by recorded PID rather than rediscovering by
  port.
* ``ManagedProcess`` — context manager wrapping ``subprocess.Popen``
  with log-file handles, PID-file persistence, readiness polling, and
  graceful-then-force termination on exit.  This replaces the ``_bg``
  closure in ``edge.start`` and the ``tee_process_output`` in
  ``comfyui.serve``.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

import psutil

from .common import echo

_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NO_WINDOW")
    else 0
)


# ── Port & process discovery ──────────────────────────────────────────────


def pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on *port* (any address)."""
    pids: set[int] = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return []
    for conn in conns:
        if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
            pids.add(conn.pid)
    return sorted(pids)


def process_name(pid: int) -> str | None:
    """Return the executable name for *pid*, or ``None`` if gone."""
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def pids_by_name(name: str) -> list[int]:
    """Return PIDs of all running processes named *name*."""
    target = name.lower().replace(".exe", "")
    matches: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pname = (proc.info["name"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pname == target or pname == f"{target}.exe":
            matches.append(proc.info["pid"])
    return sorted(matches)


# ── Kill helpers ───────────────────────────────────────────────────────────


def kill_pid(
    pid: int,
    *,
    graceful: bool = True,
    tree: bool = True,
    timeout: float = 5.0,
) -> bool:
    """Kill *pid*.  Returns ``True`` if the process is gone afterwards.

    *graceful*: if ``True`` (default), first call ``terminate()`` (SIGTERM
    on Unix, ``TerminateProcess`` on Windows) and wait up to *timeout*
    seconds.  If still alive, force-kill with ``kill()`` (SIGKILL /
    ``TerminateProcess`` with exit code 1).

    *tree*: if ``True`` (default), also kill all descendants first.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        echo(f"  Access denied trying to kill PID {pid}.")
        return False

    # Collect children before we start killing (parent may exit first).
    children = []
    if tree:
        try:
            children = proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []

    targets = [proc] + children

    # Stage 1 — graceful.
    if graceful:
        for t in targets:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                t.terminate()
        _, alive = psutil.wait_procs(targets, timeout=timeout)
        if not alive:
            return True
        targets = alive

    # Stage 2 — force.
    for t in targets:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            t.kill()
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    return not alive


def stop_on_port(
    port: int,
    *,
    graceful: bool = True,
    tree: bool = True,
    timeout: float = 5.0,
) -> int:
    """Kill all processes listening on *port*.  Returns count killed."""
    pids = pids_on_port(port)
    if not pids:
        return 0
    echo(f"  Stopping port {port} ({len(pids)} process(es): {pids})...")
    killed = 0
    for pid in pids:
        if kill_pid(pid, graceful=graceful, tree=tree, timeout=timeout):
            killed += 1
    return killed


def stop_by_name(
    name: str,
    *,
    graceful: bool = True,
    tree: bool = True,
    timeout: float = 5.0,
) -> int:
    """Kill all processes named *name*.  Returns count killed.

    Never kills the current process or its parent (safety guard).
    """
    pids = pids_by_name(name)
    if not pids:
        return 0
    # Safety: exclude ourselves and our parent.
    own = os.getpid()
    try:
        parent = os.getppid()
    except OSError:
        parent = None
    pids = [p for p in pids if p != own and p != parent]
    if not pids:
        return 0
    echo(f"  Stopping {name} ({len(pids)} process(es): {pids})...")
    killed = 0
    for pid in pids:
        if kill_pid(pid, graceful=graceful, tree=tree, timeout=timeout):
            killed += 1
    return killed


# ── Readiness polling ─────────────────────────────────────────────────────


def wait_for_url(
    url: str,
    timeout: float = 60.0,
    interval: float = 2.0,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Poll *url* until it returns HTTP 200, or *timeout* elapses.

    If *predicate* is given, the parsed-JSON body is passed to it and the
    poll succeeds only when it returns ``True``.  Otherwise any 2xx
    response is sufficient.

    Returns ``True`` if the service became ready, ``False`` on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    if predicate is None:
                        return True
                    body = resp.read().decode("utf-8", errors="replace")
                    try:
                        data = __import__("json").loads(body)
                    except Exception:
                        data = {}
                    if predicate(data):
                        return True
        except (OSError, urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(interval)
    return False


# ── PID file management ───────────────────────────────────────────────────


class PidFile:
    """Context manager for writing/reading/clearing a PID file.

    Usage::

        with PidFile(REPO_ROOT / "logs" / "edge-unsloth.pid") as pf:
            pf.write(proc.pid)
            ...
        # __exit__ clears the file

    Or standalone::

        pf = PidFile(path)
        pids = pf.read()          # list[int], empty if missing/stale
        pf.write(proc.pid)
        pf.clear()
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> PidFile:
        return self

    def __exit__(self, *exc: object) -> None:
        self.clear()

    def write(self, pid: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(pid), encoding="utf-8")

    def read(self) -> list[int]:
        """Return live PIDs from the file (skips dead ones)."""
        if not self.path.is_file():
            return []
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return []
        pids: list[int] = []
        for chunk in raw.replace(",", "\n").splitlines():
            chunk = chunk.strip()
            if chunk.isdigit():
                pid = int(chunk)
                if psutil.pid_exists(pid):
                    pids.append(pid)
        return pids

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)


# ── ManagedProcess ────────────────────────────────────────────────────────


class ManagedProcess:
    """A background subprocess with log redirection and PID persistence.

    Used as a context manager::

        with ManagedProcess(
            args=[str(py), "-m", "win_models.cli", "unsloth", "serve", ...],
            log_name="edge-unsloth",
            log_dir=log_dir,
            cwd=REPO_ROOT,
        ) as proc:
            proc.wait_ready("http://127.0.0.1:8888/v1/models", timeout=60)

    On normal exit the context manager calls ``proc.terminate()`` which
    gracefully-then-forcefully kills the process tree and closes log
    handles.  The PID file is cleared.
    """

    def __init__(
        self,
        *,
        args: Sequence[str],
        log_name: str,
        log_dir: Path,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        pid_file: Path | None = None,
    ) -> None:
        self.args = list(args)
        self.log_name = log_name
        self.log_dir = Path(log_dir)
        self.cwd = cwd
        self.env = env
        self._out_fh: Any = None
        self._err_fh: Any = None
        self.proc: subprocess.Popen | None = None
        self.pid_file = PidFile(pid_file) if pid_file else None

    # ── context manager ──────────────────────────────────────────────

    def __enter__(self) -> ManagedProcess:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.terminate()

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the subprocess, redirecting stdout/stderr to log files."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        out_log = self.log_dir / f"{self.log_name}.out.log"
        err_log = self.log_dir / f"{self.log_name}.err.log"
        # Truncate on each start so logs don't grow unbounded.
        self._out_fh = open(out_log, "w", encoding="utf-8")
        self._err_fh = open(err_log, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            self.args,
            stdout=self._out_fh,
            stderr=self._err_fh,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            creationflags=_NO_WINDOW,
        )
        if self.pid_file:
            self.pid_file.write(self.proc.pid)
        echo(f"  Started {self.log_name} (PID {self.proc.pid}) -> {out_log}")

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def poll(self) -> int | None:
        """Return exit code if the process has exited, else ``None``."""
        return self.proc.poll() if self.proc else None

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def wait_ready(
        self,
        url: str,
        timeout: float = 60.0,
        interval: float = 2.0,
    ) -> bool:
        """Poll *url* until ready or *timeout* elapses."""
        if not self.is_alive():
            return False
        ready = wait_for_url(url, timeout=timeout, interval=interval)
        return ready and self.is_alive()

    def terminate(self, *, graceful: bool = True, timeout: float = 5.0) -> None:
        """Stop the process and clean up log handles + PID file."""
        if self.proc and self.proc.poll() is None:
            kill_pid(
                self.proc.pid,
                graceful=graceful,
                tree=True,
                timeout=timeout,
            )
        if self._out_fh:
            self._out_fh.close()
            self._out_fh = None
        if self._err_fh:
            self._err_fh.close()
            self._err_fh = None
        if self.pid_file:
            self.pid_file.clear()

    def __repr__(self) -> str:
        pid = self.pid
        state = "running" if self.is_alive() else ("exited" if self.proc else "not started")
        return f"ManagedProcess({self.log_name!r}, pid={pid}, {state})"
