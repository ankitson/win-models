from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MODEL_ROOT = Path(os.environ.get("WIN_MODELS_MODEL_ROOT", r"E:\root\projects\models"))
DEFAULT_STUDIO_HOME = Path(os.environ.get("UNSLOTH_STUDIO_HOME", r"E:\root\projects\unsloth"))
DEFAULT_HOST = os.environ.get("WIN_MODELS_HOST", "0.0.0.0")
DEFAULT_LLAMA_PORT = int(os.environ.get("WIN_MODELS_LLAMA_PORT", "8080"))
DEFAULT_LITERT_PORT = int(os.environ.get("WIN_MODELS_LITERT_PORT", "9379"))
DEFAULT_STUDIO_PORT = int(os.environ.get("WIN_MODELS_STUDIO_PORT", "8888"))

