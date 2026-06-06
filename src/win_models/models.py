from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Asset:
    url: str
    filename: str


@dataclass(frozen=True)
class Variant:
    key: str
    directory: str
    alias: str
    model_file: str
    mmproj_file: str | None
    assets: tuple[Asset, ...]
    runtime: str = "llama"

    def model_path(self, root: Path) -> Path:
        return root / self.directory / self.model_file

    def mmproj_path(self, root: Path) -> Path | None:
        if self.mmproj_file is None:
            return None
        return root / self.directory / self.mmproj_file

    def directory_path(self, root: Path) -> Path:
        return root / self.directory


VARIANTS: dict[str, Variant] = {
    "google-qat12": Variant(
        key="google-qat12",
        directory="gemma-4-12b-it-qat-q4_0",
        alias="gemma-4-12b-qat",
        model_file="gemma-4-12b-it-qat-q4_0.gguf",
        mmproj_file="mmproj-gemma-4-12b-it-qat-q4_0.gguf",
        assets=(
            Asset(
                "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main/gemma-4-12b-it-qat-q4_0.gguf?download=true",
                "gemma-4-12b-it-qat-q4_0.gguf",
            ),
            Asset(
                "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main/mmproj-gemma-4-12b-it-qat-q4_0.gguf?download=true",
                "mmproj-gemma-4-12b-it-qat-q4_0.gguf",
            ),
        ),
    ),
    "ggml-12b-q4km": Variant(
        key="ggml-12b-q4km",
        directory="ggml-org-gemma-4-12b-it-q4km",
        alias="gemma-4-12b-ggml-q4km",
        model_file="gemma-4-12B-it-Q4_K_M.gguf",
        mmproj_file="mmproj-gemma-4-12B-it-Q8_0.gguf",
        assets=(
            Asset(
                "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf?download=true",
                "gemma-4-12B-it-Q4_K_M.gguf",
            ),
            Asset(
                "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/mmproj-gemma-4-12B-it-Q8_0.gguf?download=true",
                "mmproj-gemma-4-12B-it-Q8_0.gguf",
            ),
        ),
    ),
    "unsloth-26b-q3km": Variant(
        key="unsloth-26b-q3km",
        directory="unsloth-gemma-4-26b-a4b-it-ud-q3km",
        alias="gemma-4-26b-a4b-unsloth-q3km",
        model_file="gemma-4-26B-A4B-it-UD-Q3_K_M.gguf",
        mmproj_file="mmproj-F16.gguf",
        assets=(
            Asset(
                "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-UD-Q3_K_M.gguf?download=true",
                "gemma-4-26B-A4B-it-UD-Q3_K_M.gguf",
            ),
            Asset(
                "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/mmproj-F16.gguf?download=true",
                "mmproj-F16.gguf",
            ),
        ),
    ),
    "litert-e4b": Variant(
        key="litert-e4b",
        directory="litert-gemma-4-e4b-it",
        alias="gemma-4-E4B-it",
        model_file="gemma-4-E4B-it.litertlm",
        mmproj_file=None,
        runtime="litert",
        assets=(
            Asset(
                "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm?download=true",
                "gemma-4-E4B-it.litertlm",
            ),
        ),
    ),
    "litert-12b": Variant(
        key="litert-12b",
        directory="litert-gemma-4-12b-it",
        alias="gemma-4-12B-it",
        model_file="gemma-4-12B-it.litertlm",
        mmproj_file=None,
        runtime="litert",
        assets=(
            Asset(
                "https://huggingface.co/litert-community/gemma-4-12B-it-litert-lm/resolve/main/gemma-4-12B-it.litertlm?download=true",
                "gemma-4-12B-it.litertlm",
            ),
        ),
    ),
}


LLAMA_VARIANTS = tuple(key for key, variant in VARIANTS.items() if variant.runtime == "llama")
LITERT_VARIANTS = tuple(key for key, variant in VARIANTS.items() if variant.runtime == "litert")

