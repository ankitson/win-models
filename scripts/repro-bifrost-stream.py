#!/usr/bin/env python3
"""Reproduce Bifrost streaming hangs against the Windows Unsloth upstream.

Run this from desktop-linux for the default Bifrost URL, or override URLs from
Windows if Bifrost is exposed elsewhere. The script compares three legs:

1. Windows Unsloth direct.
2. Bifrost OpenAI-compatible route.
3. Bifrost OpenAI-compatible route with x-bf-passthrough-extra-params.

It prints first-byte / first-SSE / DONE timings so buffering vs upstream model
latency is obvious.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DIRECT_URL = "https://unsloth.win.ankitson.com/v1/chat/completions"
DEFAULT_BIFROST_URL = "http://127.0.0.1:8090/openai/v1/chat/completions"
DEFAULT_DIRECT_MODEL = "unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL"
DEFAULT_BIFROST_MODEL = "unsloth/unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL"


@dataclass(frozen=True)
class Leg:
    name: str
    url: str
    model: str
    headers: dict[str, str]


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def compact(value: str, limit: int = 240) -> str:
    value = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def auth_header(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def build_payload(model: str, prompt: str, max_tokens: int, stream: bool) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a streaming transport test. Be literal."},
            {"role": "user", "content": prompt},
        ],
        "stream": stream,
        "temperature": 0,
        "top_p": 1,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def decode_sse_payload(raw: str) -> tuple[str, str | None]:
    raw = raw.strip()
    if not raw.startswith("data:"):
        return "", None
    data = raw.removeprefix("data:").strip()
    if data == "[DONE]":
        return "", "[DONE]"
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return "", None
    text = ""
    for choice in obj.get("choices") or []:
        delta = choice.get("delta") or {}
        if isinstance(delta.get("content"), str):
            text += delta["content"]
        if isinstance(delta.get("reasoning_content"), str):
            text += f"<reasoning:{delta['reasoning_content']}>"
        if isinstance(choice.get("text"), str):
            text += choice["text"]
    return text, None


def run_leg(leg: Leg, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        **leg.headers,
    }
    request = urllib.request.Request(leg.url, data=body, headers=request_headers, method="POST")

    result: dict[str, Any] = {
        "leg": leg.name,
        "url": leg.url,
        "model": payload["model"],
        "status": None,
        "first_response_s": None,
        "first_data_s": None,
        "done_s": None,
        "chunks": 0,
        "data_events": 0,
        "text": "",
        "error": None,
    }

    print(f"\n== {leg.name} ==")
    print(f"url:   {leg.url}")
    print(f"model: {payload['model']}")
    print(f"headers: {', '.join(sorted(k for k in leg.headers if k.lower() != 'authorization')) or '(none)'}")

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            result["status"] = response.status
            result["first_response_s"] = round(time.perf_counter() - started, 3)
            print(f"HTTP {response.status} first_response={result['first_response_s']}s")

            while True:
                line = response.readline()
                if line == b"":
                    break

                now_s = time.perf_counter() - started
                decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if decoded:
                    result["chunks"] += 1
                if decoded.startswith("data:"):
                    result["data_events"] += 1
                    if result["first_data_s"] is None:
                        result["first_data_s"] = round(now_s, 3)
                        print(f"first_data={result['first_data_s']}s")
                    text, sentinel = decode_sse_payload(decoded)
                    result["text"] += text
                    if sentinel == "[DONE]":
                        result["done_s"] = round(now_s, 3)
                        print(f"DONE at {result['done_s']}s chunks={result['chunks']} data_events={result['data_events']}")
                        break

            if result["done_s"] is None:
                print(f"stream closed without [DONE] at {round(time.perf_counter() - started, 3)}s")

    except socket.timeout:
        result["error"] = f"socket timeout after {timeout_s}s"
        print(result["error"])
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        error_body = exc.read().decode("utf-8", errors="replace")
        result["error"] = f"HTTP {exc.code}: {compact(error_body)}"
        print(result["error"])
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic script.
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(result["error"])

    result["elapsed_s"] = round(time.perf_counter() - started, 3)
    print(f"text: {compact(result['text'])!r}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", action="append", type=Path, default=[], help="dotenv file to load before reading API keys")
    parser.add_argument("--direct-url", default=os.environ.get("UNSLOTH_DIRECT_URL", DEFAULT_DIRECT_URL))
    parser.add_argument("--bifrost-url", default=os.environ.get("BIFROST_OPENAI_URL", DEFAULT_BIFROST_URL))
    parser.add_argument("--direct-model", default=os.environ.get("UNSLOTH_DIRECT_MODEL", DEFAULT_DIRECT_MODEL))
    parser.add_argument("--bifrost-model", default=os.environ.get("BIFROST_MODEL", DEFAULT_BIFROST_MODEL))
    parser.add_argument("--direct-api-key-env", default="UNSLOTH_STUDIO_API_KEY")
    parser.add_argument("--bifrost-api-key-env", default="BIFROST_API_KEY")
    parser.add_argument("--prompt", default="Reply exactly with OK and no other text.")
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--no-direct", action="store_true", help="Skip the direct Windows Unsloth leg")
    parser.add_argument("--no-bifrost", action="store_true", help="Skip both Bifrost legs")
    parser.add_argument("--output-jsonl", type=Path, help="Append machine-readable results here")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for env_file in args.env_file:
        load_env_file(env_file)

    direct_key = os.environ.get(args.direct_api_key_env)
    bifrost_key = os.environ.get(args.bifrost_api_key_env)
    legs: list[Leg] = []

    if not args.no_direct:
        legs.append(
            Leg(
                name="direct-windows-unsloth",
                url=args.direct_url,
                model=args.direct_model,
                headers=auth_header(direct_key),
            )
        )

    if not args.no_bifrost:
        bifrost_headers = auth_header(bifrost_key)
        legs.append(Leg(name="bifrost", url=args.bifrost_url, model=args.bifrost_model, headers=bifrost_headers))
        legs.append(
            Leg(
                name="bifrost-passthrough-extra-params",
                url=args.bifrost_url,
                model=args.bifrost_model,
                headers={**bifrost_headers, "x-bf-passthrough-extra-params": "true"},
            )
        )

    if not legs:
        print("No legs selected.", file=sys.stderr)
        return 2

    results = []
    for leg in legs:
        payload = build_payload(leg.model, args.prompt, args.max_tokens, stream=True)
        results.append(run_leg(leg, payload, args.timeout))

    if args.output_jsonl:
        args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.output_jsonl.open("a", encoding="utf-8") as fh:
            for result in results:
                fh.write(json.dumps(result, sort_keys=True) + "\n")

    print("\n== summary ==")
    for result in results:
        state = "ok" if result["done_s"] is not None else "bad"
        print(
            f"{state:3} {result['leg']}: "
            f"first_response={result['first_response_s']}s "
            f"first_data={result['first_data_s']}s "
            f"done={result['done_s']}s "
            f"error={result['error']}"
        )

    return 1 if any(result["done_s"] is None for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
