from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Iterable, Sequence


class CommandError(RuntimeError):
    pass


def echo(message: str = "") -> None:
    print(message, flush=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run(args: Sequence[str | os.PathLike[str]], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    printable = " ".join(str(a) for a in args)
    echo(f"> {printable}")
    completed = subprocess.run([str(a) for a in args], cwd=cwd, env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def output(args: Sequence[str | os.PathLike[str]], *, check: bool = True) -> str:
    completed = subprocess.run(
        [str(a) for a in args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        raise CommandError(completed.stderr.strip() or f"Command failed: {' '.join(str(a) for a in args)}")
    return completed.stdout


def powershell(command: str, *, check: bool = True) -> str:
    return output(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], check=check)


def download_file(url: str, outfile: Path) -> None:
    ensure_dir(outfile.parent)
    action = "Resuming or verifying" if outfile.exists() else "Downloading"
    echo(f"{action}: {outfile}")
    run(["curl.exe", "-L", "-C", "-", url, "-o", outfile])


def wait_openai_server(base_url: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    url = base_url.rstrip("/") + "/models"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(2)
    raise TimeoutError(f"Timed out waiting for {url}")


def post_json(url: str, payload: dict, timeout_seconds: int = 300) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:  # pragma: no cover - best effort only
        echo(f"Could not open browser automatically: {exc}")


def stop_process_names(names: Iterable[str]) -> None:
    names = list(names)
    if not names:
        return
    if sys.platform == "win32":
        for name in names:
            subprocess.run(["taskkill.exe", "/F", "/T", "/IM", f"{name}.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["taskkill.exe", "/F", "/T", "/IM", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["pkill", "-f", "|".join(names)], check=False)


def print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        echo("(no rows)")
        return
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row.get(column, ""))))
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    echo(header)
    echo("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        echo("  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))

