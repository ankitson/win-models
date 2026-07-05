from __future__ import annotations

import argparse
import sys

from . import comfyui, lmstudio, parakeet_asr, plain_llama, unsloth, utils


AREAS = {
    "plain": ("Direct llama.cpp and LiteRT commands", plain_llama.main),
    "unsloth": ("Unsloth Studio commands", unsloth.main),
    "lmstudio": ("LM Studio setup and serve commands", lmstudio.main),
    "parakeet": ("Parakeet ASR sidecar commands", parakeet_asr.main),
    "comfy": ("ComfyUI setup and serve commands", comfyui.main),
    "utils": ("Windows utility commands", utils.main),
}


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(prog="win-models")
    parser.add_argument("area", choices=AREAS, help="Command area")
    if not argv:
        parser.print_help()
        raise SystemExit(2)
    if argv[0] in ("-h", "--help"):
        parser.print_help()
        return
    area = argv[0]
    if area not in AREAS:
        parser.error(f"invalid choice: {area!r} (choose from {', '.join(AREAS)})")
    AREAS[area][1](argv[1:])
