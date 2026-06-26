from __future__ import annotations

import math

import comfy.utils
import torch
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


class ImageContactSheet:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "layout": (["row", "column", "grid"],),
                "columns": ("INT", {"default": 3, "min": 1, "max": 12}),
                "match_image_size": ("BOOLEAN", {"default": True}),
                "spacing_width": ("INT", {"default": 0, "min": 0, "max": 1024, "step": 2}),
                "spacing_color": (["white", "black", "red", "green", "blue"],),
            },
            "optional": {
                f"image{i}": ("IMAGE",) for i in range(1, 13)
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "stitch"
    CATEGORY = "image/transform"
    DESCRIPTION = (
        "Stitch any connected images into a row, column, or grid. "
        "Batched IMAGE inputs are expanded into individual tiles."
    )

    @staticmethod
    def _color_value(name: str) -> tuple[float, float, float]:
        return {
            "white": (1.0, 1.0, 1.0),
            "black": (0.0, 0.0, 0.0),
            "red": (1.0, 0.0, 0.0),
            "green": (0.0, 1.0, 0.0),
            "blue": (0.0, 0.0, 1.0),
        }[name]

    @staticmethod
    def _match_channels(image: torch.Tensor, channels: int) -> torch.Tensor:
        if image.shape[-1] >= channels:
            return image
        fill = torch.ones(
            (*image.shape[:-1], channels - image.shape[-1]),
            device=image.device,
            dtype=image.dtype,
        )
        return torch.cat((image, fill), dim=-1)

    @staticmethod
    def _resize(image: torch.Tensor, width: int, height: int) -> torch.Tensor:
        if image.shape[1] == height and image.shape[2] == width:
            return image
        return comfy.utils.common_upscale(
            image.movedim(-1, 1),
            width,
            height,
            "lanczos",
            "center",
        ).movedim(1, -1)

    @staticmethod
    def _blank(
        height: int,
        width: int,
        channels: int,
        color: tuple[float, float, float],
        *,
        device: torch.device,
        dtype: torch.dtype,
    ) -> torch.Tensor:
        tile = torch.zeros((1, height, width, channels), device=device, dtype=dtype)
        for index, value in enumerate(color):
            if index < channels:
                tile[..., index] = value
        if channels == 4:
            tile[..., 3] = 1.0
        return tile

    @staticmethod
    def _pad_to(
        image: torch.Tensor,
        width: int,
        height: int,
        color: tuple[float, float, float],
    ) -> torch.Tensor:
        if image.shape[1] == height and image.shape[2] == width:
            return image
        out = ImageContactSheet._blank(
            height,
            width,
            image.shape[-1],
            color,
            device=image.device,
            dtype=image.dtype,
        )
        top = (height - image.shape[1]) // 2
        left = (width - image.shape[2]) // 2
        out[:, top : top + image.shape[1], left : left + image.shape[2], :] = image
        return out

    @staticmethod
    def _spacing(
        height: int,
        width: int,
        channels: int,
        color: tuple[float, float, float],
        reference: torch.Tensor,
    ) -> torch.Tensor:
        return ImageContactSheet._blank(
            height,
            width,
            channels,
            color,
            device=reference.device,
            dtype=reference.dtype,
        )

    def stitch(
        self,
        layout: str,
        columns: int,
        match_image_size: bool,
        spacing_width: int,
        spacing_color: str,
        **kwargs,
    ):
        images = [kwargs[key] for key in sorted(kwargs) if kwargs[key] is not None]
        tiles = [image[index : index + 1] for image in images for index in range(image.shape[0])]
        if not tiles:
            raise ValueError("At least one image input is required.")

        color = self._color_value(spacing_color)
        channels = max(tile.shape[-1] for tile in tiles)
        tiles = [self._match_channels(tile, channels) for tile in tiles]

        if match_image_size:
            tile_h, tile_w = tiles[0].shape[1:3]
            tiles = [self._resize(tile, tile_w, tile_h) for tile in tiles]
        else:
            tile_h = max(tile.shape[1] for tile in tiles)
            tile_w = max(tile.shape[2] for tile in tiles)
            tiles = [self._pad_to(tile, tile_w, tile_h, color) for tile in tiles]

        if layout == "row":
            row_count = 1
            column_count = len(tiles)
        elif layout == "column":
            row_count = len(tiles)
            column_count = 1
        else:
            column_count = max(1, min(columns, len(tiles)))
            row_count = math.ceil(len(tiles) / column_count)

        reference = tiles[0]
        blank = self._blank(
            tile_h,
            tile_w,
            channels,
            color,
            device=reference.device,
            dtype=reference.dtype,
        )
        while len(tiles) < row_count * column_count:
            tiles.append(blank.clone())

        rows = []
        spacing_width = spacing_width + (spacing_width % 2)
        for row_index in range(row_count):
            row_tiles = tiles[row_index * column_count : (row_index + 1) * column_count]
            if spacing_width > 0 and column_count > 1:
                spacer = self._spacing(tile_h, spacing_width, channels, color, reference)
                interleaved = []
                for index, tile in enumerate(row_tiles):
                    if index:
                        interleaved.append(spacer)
                    interleaved.append(tile)
                row_tiles = interleaved
            rows.append(torch.cat(row_tiles, dim=2))

        if spacing_width > 0 and row_count > 1:
            spacer = self._spacing(spacing_width, rows[0].shape[2], channels, color, reference)
            interleaved_rows = []
            for index, row in enumerate(rows):
                if index:
                    interleaved_rows.append(spacer)
                interleaved_rows.append(row)
            rows = interleaved_rows

        return (torch.cat(rows, dim=1),)


NODE_CLASS_MAPPINGS = {
    "Flux2KleinPresetLoader": Flux2KleinPresetLoader,
    "ImageContactSheet": ImageContactSheet,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Flux2KleinPresetLoader": "Flux.2 Klein Preset Loader",
    "ImageContactSheet": "Image Contact Sheet",
}
