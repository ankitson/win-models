from __future__ import annotations

import datetime
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO


class RawLogger:
    """Log raw HTTP request/response bodies and server output to a file."""

    def __init__(self, path: Path | str, label: str = "llama") -> None:
        self.path = Path(path)
        self.label = label
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> RawLogger:
        self._file = self.path.open("a", encoding="utf-8")
        self._write("--- session started ---")
        return self

    def __exit__(self, *args: object) -> None:
        self._write("--- session ended ---")
        self._file.close()

    def _write(self, text: str) -> None:
        self._file.write(text + "\n")
        self._file.flush()

    def log(self, direction: str, body: str) -> None:
        ts = datetime.datetime.now().isoformat()
        self._write(f"[{ts}] {self.label} {direction}")
        if body:
            self._write(body)
        self._write("")

    def request(self, body: str) -> None:
        self.log(">>>", body)

    def response(self, body: str) -> None:
        self.log("<<<", body)

    def write(self, text: str) -> None:
        self._file.write(text)
        self._file.flush()


def log_level() -> str | None:
    raw = (os.environ.get("WIN_MODELS_LLAMA_LOG") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "all"
    return raw if raw in ("all", "server", "api") else None


def log_path() -> Path:
    raw = (os.environ.get("WIN_MODELS_LLAMA_LOG_FILE") or "").strip()
    if raw:
        return Path(raw)
    return Path.cwd() / "logs" / "llama-server.log"


def tee_subprocess(
    args: list[str],
    log_file: Path | str | None = None,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Run a subprocess, tee-ing its output to both console and log file."""
    printable = " ".join(str(a) for a in args)
    print(f"> {printable}", flush=True)

    ts = datetime.datetime.now().isoformat()
    with open(log_file, "a", encoding="utf-8") as log:
        log.write(f"\n--- [{ts}] $ {printable} ---\n")
        log.flush()

        process = subprocess.Popen(
            [str(a) for a in args],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        def _pipe(stream: IO[bytes], log_fh: IO[str]) -> None:
            for raw in iter(stream.readline, b""):
                text = raw.decode("utf-8", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                log_fh.write(text)
                log_fh.flush()

        reader = threading.Thread(
            target=_pipe, args=(process.stdout, log), daemon=True
        )
        reader.start()
        reader.join()
        process.wait()

    if process.returncode != 0:
        raise SystemExit(process.returncode)
