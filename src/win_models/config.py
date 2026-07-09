from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MODEL_ROOT = Path(os.environ.get("WIN_MODELS_MODEL_ROOT", r"E:\root\projects\models"))
DEFAULT_STUDIO_HOME = Path(os.environ.get("UNSLOTH_STUDIO_HOME", r"E:\root\projects\unsloth"))
DEFAULT_COMFYUI_HOME = Path(os.environ.get("COMFYUI_HOME", r"E:\root\projects\comfyui"))
DEFAULT_COMFYUI_MODEL_ROOT = Path(
    os.environ.get("COMFYUI_MODEL_ROOT", str(DEFAULT_MODEL_ROOT / "comfyui"))
)
DEFAULT_HOST = os.environ.get("WIN_MODELS_HOST", "0.0.0.0")
DEFAULT_LLAMA_PORT = int(os.environ.get("WIN_MODELS_LLAMA_PORT", "8080"))
DEFAULT_LITERT_PORT = int(os.environ.get("WIN_MODELS_LITERT_PORT", "9379"))
DEFAULT_STUDIO_PORT = int(os.environ.get("WIN_MODELS_STUDIO_PORT", "8888"))
DEFAULT_LMSTUDIO_PORT = int(os.environ.get("LMSTUDIO_PORT", "1234"))
DEFAULT_COMFYUI_PORT = int(os.environ.get("COMFYUI_PORT", "8188"))

# ── Declarative model config ──────────────────────────────────────────────────


@dataclass
class ModelConfig:
    """Runtime parameters for a model variant, loaded from models-config.json."""

    key: str
    description: str
    hf_repo: str
    hf_model_ref: str
    alias: str
    context_length: int = 8192
    cache_type_kv: str = "q8_0"
    reasoning: str = "on"
    reasoning_format: str = "deepseek"
    chat_template_file: str | None = None
    gpu_layers: str = "all"
    flash_attn: bool = True
    parallel: int = 1
    llama_extra_args: str = ""

    @property
    def resolved_chat_template(self) -> str | None:
        """Resolve chat_template_file relative to the repo root, if set."""
        if not self.chat_template_file:
            return None
        p = Path(self.chat_template_file)
        if p.is_absolute():
            return str(p)
        return str(REPO_ROOT / self.chat_template_file)


def find_repo_root() -> Path:
    """Walk up from this file to find the win-models repo root (has models-config.json)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "models-config.json").is_file():
            return parent
    # Fallback: assume we are running from the repo root
    return Path.cwd()


REPO_ROOT = find_repo_root()
MODELS_CONFIG_PATH = REPO_ROOT / "models-config.json"

_model_config_cache: dict[str, ModelConfig] | None = None
_default_model_key: str | None = None


def _load_raw_config() -> dict[str, Any]:
    if not MODELS_CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"Missing declarative model config at {MODELS_CONFIG_PATH}. "
            "Create a models-config.json in the repo root."
        )
    with open(MODELS_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_model_configs() -> dict[str, ModelConfig]:
    """Load all model configs from models-config.json (cached)."""
    global _model_config_cache, _default_model_key
    if _model_config_cache is not None:
        return _model_config_cache

    raw = _load_raw_config()
    _default_model_key = raw.get("default_model", "")
    configs: dict[str, ModelConfig] = {}
    for key, data in raw.get("models", {}).items():
        configs[key] = ModelConfig(
            key=key,
            description=data.get("description", ""),
            hf_repo=data.get("hf_repo", ""),
            hf_model_ref=data.get("hf_model_ref", ""),
            alias=data.get("alias", key),
            context_length=data.get("context_length", 8192),
            cache_type_kv=data.get("cache_type_kv", "q8_0"),
            reasoning=data.get("reasoning", "on"),
            reasoning_format=data.get("reasoning_format", "deepseek"),
            chat_template_file=data.get("chat_template_file"),
            gpu_layers=data.get("gpu_layers", "all"),
            flash_attn=data.get("flash_attn", True),
            parallel=data.get("parallel", 1),
            llama_extra_args=data.get("llama_extra_args", ""),
        )
    _model_config_cache = configs
    return configs


def get_default_model_key() -> str:
    """Return the default model key from models-config.json."""
    if _default_model_key is None:
        load_model_configs()
    return _default_model_key or ""


def get_model_config(key: str) -> ModelConfig | None:
    """Get config for a specific model key, or None if not found."""
    return load_model_configs().get(key)


def list_model_keys() -> list[str]:
    """Return all available model keys."""
    return sorted(load_model_configs().keys())
