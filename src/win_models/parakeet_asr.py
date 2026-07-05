from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .common import echo, ensure_dir, run
from .config import DEFAULT_MODEL_ROOT


DEFAULT_ASR_HOME = Path(
    os.environ.get("WIN_MODELS_PARAKEET_HOME", r"E:\root\projects\parakeet-asr")
)
DEFAULT_ASR_HOST = os.environ.get("WIN_MODELS_ASR_HOST", "127.0.0.1")
DEFAULT_ASR_PORT = int(os.environ.get("WIN_MODELS_ASR_PORT", "8891"))
DEFAULT_ASR_MODEL = os.environ.get(
    "WIN_MODELS_ASR_MODEL", "nvidia/parakeet-tdt-0.6b-v2"
)
DEFAULT_TORCH_INDEX_URL = os.environ.get(
    "WIN_MODELS_ASR_TORCH_INDEX_URL", "https://download.pytorch.org/whl/cu130"
)
MAX_REQUEST_BYTES = int(
    os.environ.get("WIN_MODELS_ASR_MAX_REQUEST_BYTES", str(70 * 1024 * 1024))
)


class ParakeetService:
    def __init__(self, model_name: str, device: str, cache_dir: Path | None) -> None:
        self.model_name = model_name
        self.device = device
        self.cache_dir = cache_dir
        self._model: Any | None = None
        self._lock = threading.Lock()
        self.started_at = time.time()

    def load(self) -> None:
        if self._model is not None:
            return
        if self.cache_dir:
            cache = str(self.cache_dir)
            os.environ.setdefault("HF_HUB_CACHE", cache)
            os.environ.setdefault("HUGGINGFACE_HUB_CACHE", cache)
        echo(f"Loading {self.model_name} for ASR on {self.device}...")
        import torch
        import nemo.collections.asr as nemo_asr

        with self._lock:
            if self._model is not None:
                return
            model = nemo_asr.models.ASRModel.from_pretrained(
                model_name=self.model_name
            )
            if self.device == "cuda" and torch.cuda.is_available():
                model = model.to("cuda")
            elif self.device == "cpu":
                model = model.to("cpu")
            model.eval()
            self._model = model
        echo("Parakeet ASR model loaded.")

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def transcribe_wav_base64(self, audio_base64: str) -> dict[str, Any]:
        self.load()
        assert self._model is not None
        if audio_base64.startswith("data:"):
            audio_base64 = audio_base64.split(",", 1)[1] if "," in audio_base64 else ""
        audio_bytes = base64.b64decode(audio_base64)
        with tempfile.TemporaryDirectory(prefix="parakeet-asr-") as tmp_dir:
            wav_path = Path(tmp_dir) / "input.wav"
            wav_path.write_bytes(audio_bytes)
            started = time.perf_counter()
            with self._lock:
                output = self._model.transcribe([str(wav_path)])
            elapsed = time.perf_counter() - started
        first = output[0] if output else ""
        text = getattr(first, "text", first)
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return {
            "text": str(text).strip(),
            "model": self.model_name,
            "elapsed_s": elapsed,
        }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _make_handler(service: ParakeetService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "win-models-parakeet-asr/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            echo(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:
            if self.path.rstrip("/") != "/health":
                _json_response(self, 404, {"error": "not found"})
                return
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "loaded": service.loaded,
                    "model": service.model_name,
                    "uptime_s": time.time() - service.started_at,
                },
            )

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/transcribe":
                _json_response(self, 404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                _json_response(self, 400, {"error": "invalid content length"})
                return
            if length <= 0:
                _json_response(self, 400, {"error": "empty request"})
                return
            if length > MAX_REQUEST_BYTES:
                _json_response(self, 413, {"error": "request too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
                audio_base64 = str(payload.get("audio_base64") or "")
                if not audio_base64:
                    raise ValueError("audio_base64 is required")
                result = service.transcribe_wav_base64(audio_base64)
                _json_response(self, 200, result)
            except Exception as exc:
                _json_response(self, 500, {"error": str(exc)})

    return Handler


def setup(args: argparse.Namespace) -> None:
    home = Path(args.home)
    venv = home / ".venv"
    python = venv / "Scripts" / "python.exe"
    ensure_dir(home)
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to create the Parakeet ASR environment")

    if python.exists():
        echo(f"Reusing existing Parakeet ASR environment at {venv}")
    else:
        run([uv, "venv", str(venv), "--python", args.python])
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            python,
            "--index-url",
            args.torch_index_url,
            "torch",
            "torchaudio",
        ]
    )
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            python,
            "--extra-index-url",
            args.torch_index_url,
            "--index-strategy",
            "unsafe-best-match",
            "nemo_toolkit[asr]",
            "numba>=0.62",
            "llvmlite>=0.45",
            "soundfile",
        ]
    )
    echo(f"Parakeet ASR environment ready at {venv}")
    echo(f"Run: {python} -m win_models.parakeet_asr serve")


def serve(args: argparse.Namespace) -> None:
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if cache_dir:
        ensure_dir(cache_dir)
    service = ParakeetService(args.model, args.device, cache_dir)
    if args.preload:
        service.load()
    server = ThreadingHTTPServer(
        (args.host, args.port),
        _make_handler(service),
    )
    echo(f"Parakeet ASR listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def stop(args: argparse.Namespace) -> None:
    command = (
        f"Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue "
        "| ForEach-Object { $_.OwningProcess }"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    pids = {
        int(line.strip())
        for line in completed.stdout.splitlines()
        if line.strip().isdigit()
    }
    if not pids:
        echo(f"No Parakeet ASR server found on port {args.port}.")
        return
    for pid in sorted(pids):
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    echo(f"Stopped Parakeet ASR server on port {args.port}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models parakeet")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup")
    p.add_argument("--home", default=str(DEFAULT_ASR_HOME))
    p.add_argument("--python", default=os.environ.get("WIN_MODELS_ASR_PYTHON", "3.13"))
    p.add_argument("--torch-index-url", default=DEFAULT_TORCH_INDEX_URL)
    p.set_defaults(func=setup)

    p = sub.add_parser("serve")
    p.add_argument("--host", default=DEFAULT_ASR_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_ASR_PORT)
    p.add_argument("--model", default=DEFAULT_ASR_MODEL)
    p.add_argument("--device", choices=("cuda", "cpu"), default=os.environ.get("WIN_MODELS_ASR_DEVICE", "cuda"))
    p.add_argument("--cache-dir", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--preload", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=serve)

    p = sub.add_parser("stop")
    p.add_argument("--port", type=int, default=DEFAULT_ASR_PORT)
    p.set_defaults(func=stop)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
