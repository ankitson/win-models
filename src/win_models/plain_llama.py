from __future__ import annotations

import argparse
import time
from pathlib import Path

from .common import download_file, echo, ensure_dir, post_json, print_table, run, stop_process_names
from .config import DEFAULT_HOST, DEFAULT_LITERT_PORT, DEFAULT_LLAMA_PORT, DEFAULT_MODEL_ROOT
from .models import LITERT_VARIANTS, LLAMA_VARIANTS, VARIANTS


def llama_run_dir(model_root: Path) -> Path:
    run_dir = model_root / "llama-b9536-cuda12" / "run"
    exe = run_dir / "llama-server.exe"
    if not exe.exists():
        raise FileNotFoundError(f"Missing llama.cpp server at {exe}. Download/extract llama.cpp b9536 CUDA first.")
    return run_dir


def download(args: argparse.Namespace) -> None:
    model_root = Path(args.model_root)
    ensure_dir(model_root)
    variant = VARIANTS[args.variant]
    target_dir = variant.directory_path(model_root)
    for asset in variant.assets:
        download_file(asset.url, target_dir / asset.filename)
    echo(f"Downloaded {variant.key} under {model_root}")


def serve(args: argparse.Namespace) -> None:
    model_root = Path(args.model_root)
    variant = VARIANTS[args.variant]
    if variant.runtime != "llama":
        raise ValueError(f"{variant.key} is a {variant.runtime} variant, not a llama.cpp variant")

    model = variant.model_path(model_root)
    mmproj = variant.mmproj_path(model_root)
    missing = [path for path in (model, mmproj) if path is not None and not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing {missing[0]}. Run: just plain download {variant.key}")

    stop_process_names(["llama-server", "litert-lm"])
    media = variant.directory_path(model_root) / "media"
    ensure_dir(media)
    run_dir = llama_run_dir(model_root)

    command = [
        run_dir / "llama-server.exe",
        "-m",
        model,
        "--mmproj",
        mmproj,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--alias",
        variant.alias,
        "--ctx-size",
        str(args.context_size),
        "--parallel",
        "1",
        "--n-gpu-layers",
        str(args.gpu_layers),
        "--flash-attn",
        "on",
        "--cache-type-k",
        "q8_0",
        "--cache-type-v",
        "q8_0",
        "--media-path",
        media,
        "--cache-ram",
        str(args.cache_ram),
        "--jinja",
        "--reasoning",
        args.reasoning,
        "--reasoning-format",
        "deepseek",
        "--sleep-idle-seconds",
        "120",
    ]
    echo(f"Starting {variant.key} as {variant.alias} on http://{args.host}:{args.port}/v1")
    echo("Server output will stream in this terminal. Press Ctrl+C to stop.")
    run(command, cwd=run_dir)


def serve_litert(args: argparse.Namespace) -> None:
    model_root = Path(args.model_root)
    variant = VARIANTS[args.variant]
    if variant.runtime != "litert":
        raise ValueError(f"{variant.key} is a {variant.runtime} variant, not a LiteRT variant")

    model = variant.model_path(model_root)
    if not model.exists():
        raise FileNotFoundError(f"Missing {model}. Run: just plain download {variant.key}")

    stop_process_names(["llama-server", "litert-lm"])
    run(["litert-lm", "import", model, variant.alias])
    echo(f"Starting LiteRT-LM {variant.key} on http://{args.host}:{args.port}/v1")
    echo(f"Use model id: {variant.alias},gpu")
    run(["litert-lm", "serve", "--host", args.host, "--port", str(args.port), "--verbose"])


def bench(args: argparse.Namespace) -> None:
    prompt = "Think briefly, then answer in exactly one sentence: for local coding use, name one advantage and one drawback of quantized models."
    rows: list[dict] = []
    for i in range(1, args.runs + 1):
        body = {
            "model": args.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": args.max_tokens,
            "temperature": 0,
        }
        start = time.perf_counter()
        response = post_json(args.base_url.rstrip("/") + "/chat/completions", body)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        usage = response.get("usage") or {}
        timings = response.get("timings") or {}
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""
        rows.append(
            {
                "run": i,
                "model": args.model,
                "elapsed_ms": elapsed_ms,
                "prompt_tokens": usage.get("prompt_tokens", ""),
                "completion_tokens": usage.get("completion_tokens", ""),
                "prompt_tps": round(timings["prompt_per_second"], 2) if "prompt_per_second" in timings else "",
                "generation_tps": round(timings["predicted_per_second"], 2) if "predicted_per_second" in timings else "",
                "finish": choice.get("finish_reason", ""),
                "content_chars": len(content),
                "reasoning_chars": len(reasoning),
            }
        )
    print_table(
        rows,
        [
            "run",
            "model",
            "elapsed_ms",
            "prompt_tokens",
            "completion_tokens",
            "prompt_tps",
            "generation_tps",
            "finish",
            "content_chars",
            "reasoning_chars",
        ],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models plain")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download")
    p.add_argument("variant", choices=sorted(VARIANTS))
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.set_defaults(func=download)

    p = sub.add_parser("serve")
    p.add_argument("variant", choices=LLAMA_VARIANTS)
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_LLAMA_PORT)
    p.add_argument("--reasoning", choices=("on", "off", "auto"), default="on")
    p.add_argument("--context-size", type=int, default=8192)
    p.add_argument("--gpu-layers", default="all")
    p.add_argument("--cache-ram", type=int, default=8192)
    p.set_defaults(func=serve)

    p = sub.add_parser("serve-litert")
    p.add_argument("variant", choices=LITERT_VARIANTS)
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_LITERT_PORT)
    p.set_defaults(func=serve_litert)

    p = sub.add_parser("bench")
    p.add_argument("model")
    p.add_argument("--base-url", default=f"http://127.0.0.1:{DEFAULT_LLAMA_PORT}/v1")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--max-tokens", type=int, default=256)
    p.set_defaults(func=bench)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)

