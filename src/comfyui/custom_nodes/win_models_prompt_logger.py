from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from server import PromptServer


def _enabled() -> bool:
    value = os.environ.get("COMFYUI_LOG_PROMPTS", "0").lower()
    return value in {"1", "true", "yes", "on"}


def _log_path() -> Path:
    configured = os.environ.get("COMFYUI_PROMPT_LOG_FILE")
    if configured:
        return Path(configured)
    return Path.cwd() / "logs" / "comfyui.prompts.jsonl"


def _register_prompt_logger() -> None:
    if not _enabled():
        return

    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def log_prompt(json_data):
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "unix_ms": int(time.time() * 1000),
            "payload": json_data,
        }
        with path.open("a", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        return json_data

    PromptServer.instance.add_on_prompt_handler(log_prompt)
    logging.info("win-models prompt logging enabled: %s", path)


_register_prompt_logger()


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
