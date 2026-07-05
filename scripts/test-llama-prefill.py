#!/usr/bin/env python3
"""Probe llama.cpp assistant-prefill behavior against a live llama-server.

This intentionally calls llama.cpp directly, not the Unsloth proxy. It covers
the request shapes we care about for edit -> Save & Continue and /asst.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_LLAMA_PORT = int(os.environ.get("UNSLOTH_LLAMA_PORT") or os.environ.get("WIN_MODELS_LLAMA_PORT") or "8080")
DEFAULT_LLAMA_BASE_URL = (
    os.environ.get("WIN_MODELS_LLAMA_BASE_URL")
    or os.environ.get("LLAMA_BASE_URL")
    or f"http://127.0.0.1:{DEFAULT_LLAMA_PORT}"
)
DEFAULT_CHAT_TEMPLATE_DIR = Path("src/unsloth/chat-templates")
DEFAULT_MODEL = "unsloth/gemma-4-12B-it-qat-GGUF"

EDIT_PREFILL = "My name is Marcus Antonio LaVerde"
STORY_PREFILL = (
    "My name is Marcus Antonio LaVerde - let me tell you the story of my life, "
    "beginning in 1860 Florence"
)
ASST_PREFILL = "In the morning"
PREVIOUS_ASSISTANT = (
    "My name is Marcus Antonio LaVerde - let me tell you the story of my life, "
    "beginning in 1860 Florence."
)

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


@dataclass(frozen=True)
class Case:
    name: str
    endpoint: str
    payload: dict[str, Any]
    prefix: str
    note: str
    metadata: dict[str, Any] = field(default_factory=dict)


def compact(text: str, limit: int = 260) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def strip_prefix(text: str, prefix: str) -> str:
    if prefix and text.startswith(prefix):
        return text[len(prefix) :]
    return text


def sanitize_case_part(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-").lower()


def raise_template_exception(message: str) -> None:
    raise RuntimeError(f"chat template raised: {message}")


def load_chat_template(template_path: Path) -> Any:
    try:
        from jinja2.sandbox import ImmutableSandboxedEnvironment
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Jinja chat-template cases require jinja2. "
            "Run this via `uv run python scripts/test-llama-prefill.py ...` "
            "or install the project dependencies."
        ) from exc

    text = template_path.read_text(encoding="utf-8")
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.globals["raise_exception"] = raise_template_exception
    env.globals["strftime_now"] = lambda fmt: dt.datetime.now().strftime(fmt)
    return env.from_string(text)


def render_chat_template(
    template_path: Path,
    messages: list[dict[str, Any]],
    *,
    add_generation_prompt: bool,
    enable_thinking: bool,
    tools: list[dict[str, Any]] | None,
    bos_token: str,
) -> str:
    template = load_chat_template(template_path)
    return template.render(
        messages=messages,
        add_generation_prompt=add_generation_prompt,
        enable_thinking=enable_thinking,
        tools=tools or [],
        bos_token=bos_token,
    )


def trim_final_turn_end(prompt: str) -> str:
    for suffix in ("<turn|>\n", "<turn|>"):
        if prompt.endswith(suffix):
            return prompt[: -len(suffix)]
    raise RuntimeError("rendered prompt did not end with a Gemma <turn|> marker")


def template_prefill_prompt(
    template_path: Path,
    messages_with_prefill: list[dict[str, Any]],
    *,
    prefix: str,
    mode: str,
    enable_thinking: bool,
    tools: list[dict[str, Any]] | None,
    bos_token: str,
) -> str:
    if mode == "final-assistant-trim":
        rendered = render_chat_template(
            template_path,
            messages_with_prefill,
            add_generation_prompt=False,
            enable_thinking=enable_thinking,
            tools=tools,
            bos_token=bos_token,
        )
        return trim_final_turn_end(rendered)

    if mode == "generation-prompt-append":
        if not messages_with_prefill or messages_with_prefill[-1].get("role") != "assistant":
            raise RuntimeError("generation-prompt-append requires a final assistant prefill message")
        history = messages_with_prefill[:-1]
        rendered = render_chat_template(
            template_path,
            history,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
            tools=tools,
            bos_token=bos_token,
        )
        return rendered + prefix

    raise ValueError(f"Unknown template prefill mode: {mode}")


def open_json_stream(url: str, payload: dict[str, Any], timeout_s: float) -> tuple[int, str, list[dict[str, Any]]]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.time()
    chunks: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            while True:
                if time.time() - started > timeout_s:
                    raise TimeoutError(f"stream exceeded {timeout_s:.1f}s")
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    chunks.append({"_raw": line})
            return response.status, "", chunks
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), chunks


def collect_stream_text(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_call_chunks: list[Any] = []
    finish_reasons: list[str] = []
    usage = None
    timings = None

    for chunk in chunks:
        if "content" in chunk and isinstance(chunk["content"], str):
            # llama.cpp /completion stream shape.
            content_parts.append(chunk["content"])
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                finish_reasons.append(str(finish_reason))
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    content_parts.append(content)
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str):
                    reasoning_parts.append(reasoning)
                tool_calls = delta.get("tool_calls")
                if tool_calls:
                    tool_call_chunks.append(tool_calls)
        if chunk.get("usage"):
            usage = chunk["usage"]
        if chunk.get("timings"):
            timings = chunk["timings"]

    return {
        "content": "".join(content_parts),
        "reasoning": "".join(reasoning_parts),
        "tool_call_chunks": tool_call_chunks,
        "finish_reasons": finish_reasons,
        "usage": usage,
        "timings": timings,
    }


def raw_gemma_prompt_for_edit(prefix: str) -> str:
    return f"<|turn>user\nwhat is your name<turn|>\n<|turn>model\n{prefix}"


def raw_gemma_prompt_for_asst_bridge(prefix: str, bridge: str) -> str:
    return (
        f"<|turn>user\nwhat is your name<turn|>\n"
        f"<|turn>model\n{PREVIOUS_ASSISTANT}<turn|>\n"
        f"<|turn>user\n{bridge}<turn|>\n"
        f"<|turn>model\n{prefix}"
    )


def chat_payload(
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    tools: bool = False,
    chat_template_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 1,
    }
    if tools:
        payload["tools"] = TOOL_SCHEMA
        payload["tool_choice"] = "auto"
    if chat_template_kwargs:
        payload["chat_template_kwargs"] = chat_template_kwargs
    return payload


def completion_payload(
    prompt: str,
    max_tokens: int,
    temperature: float,
    stop: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "stream": True,
        "n_predict": max_tokens,
        "temperature": temperature,
        "top_p": 1,
    }
    if stop:
        payload["stop"] = stop
    return payload


def prefill_message_sets() -> dict[str, tuple[list[dict[str, str]], str, str]]:
    edit_messages = [
        {"role": "user", "content": "what is your name"},
        {"role": "assistant", "content": EDIT_PREFILL},
    ]
    story_edit_messages = [
        {"role": "user", "content": "what is your name"},
        {"role": "assistant", "content": STORY_PREFILL},
    ]
    asst_empty_bridge = [
        {"role": "user", "content": "what is your name"},
        {"role": "assistant", "content": PREVIOUS_ASSISTANT},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": ASST_PREFILL},
    ]
    asst_instruction_bridge = [
        {"role": "user", "content": "what is your name"},
        {"role": "assistant", "content": PREVIOUS_ASSISTANT},
        {"role": "user", "content": "Continue the assistant response from the provided prefix."},
        {"role": "assistant", "content": ASST_PREFILL},
    ]

    return {
        "edit_prefill": (
            edit_messages,
            EDIT_PREFILL,
            "Simulates edit -> Save & Continue: final assistant message is the prefix.",
        ),
        "edit_story_prefill": (
            story_edit_messages,
            STORY_PREFILL,
            "Longer fake-identity prefix, closer to the Marcus story edit test.",
        ),
        "asst_empty_bridge": (
            asst_empty_bridge,
            ASST_PREFILL,
            "Simulates /asst after assistant with an empty user bridge.",
        ),
        "asst_instruction_bridge": (
            asst_instruction_bridge,
            ASST_PREFILL,
            "Simulates /asst after assistant with an explicit continuation bridge.",
        ),
    }


def build_cases(model: str, max_tokens: int, temperature: float) -> list[Case]:
    message_sets = prefill_message_sets()
    edit_messages, _, _ = message_sets["edit_prefill"]
    story_edit_messages, _, _ = message_sets["edit_story_prefill"]
    asst_empty_bridge, _, _ = message_sets["asst_empty_bridge"]
    asst_instruction_bridge, _, _ = message_sets["asst_instruction_bridge"]

    return [
        Case(
            "chat.edit_prefill",
            "/v1/chat/completions",
            chat_payload(model, edit_messages, max_tokens, temperature),
            EDIT_PREFILL,
            "Simulates edit -> Save & Continue: final assistant message is the prefix.",
        ),
        Case(
            "chat.edit_prefill.reasoning_off",
            "/v1/chat/completions",
            chat_payload(
                model,
                edit_messages,
                max_tokens,
                temperature,
                chat_template_kwargs={"enable_thinking": False},
            ),
            EDIT_PREFILL,
            "Same shape with llama.cpp chat_template_kwargs.enable_thinking=false.",
        ),
        Case(
            "chat.edit_prefill.tools_present",
            "/v1/chat/completions",
            chat_payload(model, edit_messages, max_tokens, temperature, tools=True),
            EDIT_PREFILL,
            "Same shape with an OpenAI function tool schema present.",
        ),
        Case(
            "chat.edit_story_prefill",
            "/v1/chat/completions",
            chat_payload(model, story_edit_messages, max_tokens, temperature),
            STORY_PREFILL,
            "Longer fake-identity prefix, closer to the Marcus story edit test.",
        ),
        Case(
            "chat.edit_story_prefill.reasoning_off",
            "/v1/chat/completions",
            chat_payload(
                model,
                story_edit_messages,
                max_tokens,
                temperature,
                chat_template_kwargs={"enable_thinking": False},
            ),
            STORY_PREFILL,
            "Longer fake-identity prefix with enable_thinking=false.",
        ),
        Case(
            "chat.edit_story_prefill.tools_present",
            "/v1/chat/completions",
            chat_payload(model, story_edit_messages, max_tokens, temperature, tools=True),
            STORY_PREFILL,
            "Longer fake-identity prefix with an OpenAI function tool schema present.",
        ),
        Case(
            "chat.invalid_double_assistant_tail",
            "/v1/chat/completions",
            chat_payload(
                model,
                [
                    {"role": "user", "content": "what is your name"},
                    {"role": "assistant", "content": PREVIOUS_ASSISTANT},
                    {"role": "assistant", "content": ASST_PREFILL},
                ],
                max_tokens,
                temperature,
            ),
            ASST_PREFILL,
            "Invalid tail shape that explains why /asst needs a bridge after an assistant turn.",
        ),
        Case(
            "chat.asst_empty_bridge",
            "/v1/chat/completions",
            chat_payload(model, asst_empty_bridge, max_tokens, temperature),
            ASST_PREFILL,
            "Simulates /asst after assistant with an empty user bridge.",
        ),
        Case(
            "chat.asst_instruction_bridge",
            "/v1/chat/completions",
            chat_payload(model, asst_instruction_bridge, max_tokens, temperature),
            ASST_PREFILL,
            "Simulates /asst after assistant with an explicit continuation bridge.",
        ),
        Case(
            "completion.raw_edit_open_turn",
            "/completion",
            completion_payload(raw_gemma_prompt_for_edit(EDIT_PREFILL), max_tokens, temperature),
            EDIT_PREFILL,
            "Raw /completion prompt ending inside the model turn.",
        ),
        Case(
            "completion.raw_story_edit_open_turn",
            "/completion",
            completion_payload(raw_gemma_prompt_for_edit(STORY_PREFILL), max_tokens, temperature),
            STORY_PREFILL,
            "Raw /completion prompt ending inside a longer fake-identity model turn.",
        ),
        Case(
            "completion.raw_asst_empty_bridge",
            "/completion",
            completion_payload(raw_gemma_prompt_for_asst_bridge(ASST_PREFILL, ""), max_tokens, temperature),
            ASST_PREFILL,
            "Raw /completion prompt for /asst with an empty user bridge.",
        ),
        Case(
            "completion.raw_asst_instruction_bridge",
            "/completion",
            completion_payload(
                raw_gemma_prompt_for_asst_bridge(
                    ASST_PREFILL,
                    "Continue the assistant response from the provided prefix.",
                ),
                max_tokens,
                temperature,
            ),
            ASST_PREFILL,
            "Raw /completion prompt for /asst with an explicit continuation bridge.",
        ),
    ]


def build_template_cases(
    template_paths: list[Path],
    max_tokens: int,
    temperature: float,
    *,
    modes: list[str],
    thinking_values: list[bool],
    include_tools: bool,
    bos_token: str,
) -> list[Case]:
    cases: list[Case] = []
    tool_variants: list[tuple[str, list[dict[str, Any]] | None]] = [("no_tools", None)]
    if include_tools:
        tool_variants.append(("tools", TOOL_SCHEMA))

    for template_path in template_paths:
        if not template_path.is_file():
            raise FileNotFoundError(f"Chat template not found: {template_path}")

        template_id = sanitize_case_part(template_path.stem)
        for message_set_name, (messages, prefix, note) in prefill_message_sets().items():
            for mode in modes:
                for enable_thinking in thinking_values:
                    for tools_label, tools in tool_variants:
                        prompt = template_prefill_prompt(
                            template_path,
                            messages,
                            prefix=prefix,
                            mode=mode,
                            enable_thinking=enable_thinking,
                            tools=tools,
                            bos_token=bos_token,
                        )
                        thinking_label = "thinking_on" if enable_thinking else "thinking_off"
                        name = (
                            f"completion.template.{template_id}.{mode}."
                            f"{message_set_name}.{thinking_label}.{tools_label}"
                        )
                        cases.append(
                            Case(
                                name,
                                "/completion",
                                completion_payload(prompt, max_tokens, temperature, stop=["<turn|>"]),
                                prefix,
                                f"Jinja-rendered {template_path.name}; {note}",
                                metadata={
                                    "template": str(template_path),
                                    "render_mode": mode,
                                    "enable_thinking": enable_thinking,
                                    "tools": bool(tools),
                                    "bos_token": bos_token,
                                    "prompt_chars": len(prompt),
                                    "prompt_ends_with_prefix": prompt.endswith(prefix),
                                    "prompt_tail": prompt[-700:],
                                    "stop": ["<turn|>"],
                                },
                            )
                        )

    return cases


def resolve_template_paths(chat_templates: list[Path], no_default_chat_template: bool) -> tuple[list[Path], bool]:
    if chat_templates:
        return chat_templates, True

    env_template = (os.environ.get("UNSLOTH_CHAT_TEMPLATE_FILE") or "").strip()
    if env_template:
        return [Path(env_template)], True

    default_template = DEFAULT_CHAT_TEMPLATE_DIR / "gemma-4-31b-it-pr118.jinja"
    if not no_default_chat_template and default_template.is_file():
        return [default_template], False

    return [], False


def parse_template_modes(value: str) -> list[str]:
    if value == "both":
        return ["final-assistant-trim", "generation-prompt-append"]
    return [value]


def parse_template_thinking(value: str) -> list[bool]:
    if value == "both":
        return [False, True]
    return [value == "on"]


def run_case(base_url: str, case: Case, timeout_s: float) -> dict[str, Any]:
    status, error, chunks = open_json_stream(base_url + case.endpoint, case.payload, timeout_s)
    collected = collect_stream_text(chunks)
    content = collected["content"]
    return {
        "name": case.name,
        "endpoint": case.endpoint,
        "note": case.note,
        "status": status,
        "error": error,
        "prefix": case.prefix,
        "raw_content": content,
        "raw_starts_with_prefix": bool(case.prefix and content.startswith(case.prefix)),
        "after_prefix_strip": strip_prefix(content, case.prefix),
        "reasoning": collected["reasoning"],
        "tool_call_chunk_count": len(collected["tool_call_chunks"]),
        "finish_reasons": collected["finish_reasons"],
        "usage": collected["usage"],
        "timings": collected["timings"],
        "metadata": case.metadata,
    }


def print_result(result: dict[str, Any]) -> None:
    print("\n" + "=" * 88)
    print(f"{result['name']}  [{result['endpoint']}]")
    print(result["note"])
    print(f"status: {result['status']}")
    metadata = result.get("metadata") or {}
    if metadata:
        summary_keys = ("template", "render_mode", "enable_thinking", "tools", "prompt_chars", "prompt_ends_with_prefix")
        summary = {key: metadata[key] for key in summary_keys if key in metadata}
        print("metadata:", json.dumps(summary, sort_keys=True))
        if "prompt_tail" in metadata:
            print("prompt_tail:", compact(metadata["prompt_tail"], 420))
    if result["error"]:
        print("error:", compact(result["error"], 600))
        return
    print(f"raw_starts_with_prefix: {result['raw_starts_with_prefix']}")
    print(f"tool_call_chunks: {result['tool_call_chunk_count']}")
    if result["finish_reasons"]:
        print("finish_reasons:", ", ".join(result["finish_reasons"]))
    if result["usage"]:
        print("usage:", json.dumps(result["usage"], sort_keys=True))
    if result["timings"]:
        timing = result["timings"]
        summary = {k: timing.get(k) for k in ("prompt_n", "predicted_n", "prompt_ms", "predicted_ms") if k in timing}
        print("timings:", json.dumps(summary, sort_keys=True))
    print("raw:", compact(result["raw_content"]))
    print("after_prefix_strip:", compact(result["after_prefix_strip"]))
    if result["reasoning"]:
        print("reasoning:", compact(result["reasoning"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_LLAMA_BASE_URL, help="llama-server base URL.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--chat-template",
        action="append",
        type=Path,
        default=[],
        help="Jinja chat template path to render into /completion prompts. Can be passed multiple times.",
    )
    parser.add_argument(
        "--no-default-chat-template",
        action="store_true",
        help="Do not auto-use the repo Gemma template when UNSLOTH_CHAT_TEMPLATE_FILE is unset.",
    )
    parser.add_argument(
        "--template-mode",
        choices=("both", "final-assistant-trim", "generation-prompt-append"),
        default="both",
        help="How to turn final assistant content into a raw /completion prefill prompt.",
    )
    parser.add_argument(
        "--template-thinking",
        choices=("off", "on", "both"),
        default="off",
        help="Value passed to the Jinja enable_thinking variable for template-rendered cases.",
    )
    parser.add_argument(
        "--template-tools",
        action="store_true",
        help="Also render template cases with the test tool schema injected.",
    )
    parser.add_argument("--bos-token", default="<bos>", help="bos_token value passed to Jinja templates.")
    parser.add_argument("--json-out", type=Path, help="Optional path to write full JSON results")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    template_paths, template_explicit = resolve_template_paths(args.chat_template, args.no_default_chat_template)
    print(f"llama-server: {base_url}")
    print(f"model: {args.model}")
    print(f"max_tokens: {args.max_tokens}, temperature: {args.temperature}")
    if template_paths:
        print("chat_templates:", ", ".join(str(path) for path in template_paths))

    cases = build_cases(args.model, args.max_tokens, args.temperature)
    if template_paths:
        try:
            cases.extend(
                build_template_cases(
                    template_paths,
                    args.max_tokens,
                    args.temperature,
                    modes=parse_template_modes(args.template_mode),
                    thinking_values=parse_template_thinking(args.template_thinking),
                    include_tools=args.template_tools,
                    bos_token=args.bos_token,
                )
            )
        except RuntimeError as exc:
            if template_explicit:
                raise
            print(f"warning: skipping default chat-template cases: {exc}", file=sys.stderr)

    results: list[dict[str, Any]] = []
    for case in cases:
        result = run_case(base_url, case, args.timeout)
        results.append(result)
        print_result(result)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
