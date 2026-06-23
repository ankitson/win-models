from __future__ import annotations

from nodes import CLIPLoader, UNETLoader, VAELoader


FLUX2_KLEIN_PRESETS = {
    "Flux.2 Klein 9B Base": {
        "unet_name": "flux2Klein_9b.safetensors",
        "clip_name": "qwen_3_8b_fp8mixed.safetensors",
        "vae_name": "full_encoder_small_decoder.safetensors",
    },
    "Flux.2 Klein 4B Distilled": {
        "unet_name": "flux-2-klein-4b-fp8.safetensors",
        "clip_name": "qwen_3_4b.safetensors",
        "vae_name": "flux2-vae.safetensors",
    },
}


class Flux2KleinPresetLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "preset": (list(FLUX2_KLEIN_PRESETS),),
                "weight_dtype": (
                    ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"],
                    {"advanced": True},
                ),
                "clip_device": (["default", "cpu"], {"advanced": True}),
                "unet_override": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional diffusion model filename. Empty uses the selected preset.",
                    },
                ),
                "clip_override": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional text encoder filename. Empty uses the selected preset.",
                    },
                ),
                "vae_override": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional VAE filename. Empty uses the selected preset.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP", "VAE")
    RETURN_NAMES = ("MODEL", "CLIP", "VAE")
    FUNCTION = "load"
    CATEGORY = "win-models/loaders"

    def load(
        self,
        preset: str,
        weight_dtype: str,
        clip_device: str,
        unet_override: str,
        clip_override: str,
        vae_override: str,
    ):
        values = FLUX2_KLEIN_PRESETS[preset]
        unet_name = unet_override.strip() or values["unet_name"]
        clip_name = clip_override.strip() or values["clip_name"]
        vae_name = vae_override.strip() or values["vae_name"]

        model = UNETLoader().load_unet(unet_name, weight_dtype)[0]
        clip = CLIPLoader().load_clip(clip_name, "flux2", clip_device)[0]
        vae = VAELoader().load_vae(vae_name)[0]
        return (model, clip, vae)


NODE_CLASS_MAPPINGS = {
    "Flux2KleinPresetLoader": Flux2KleinPresetLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Flux2KleinPresetLoader": "Flux.2 Klein Preset Loader",
}
