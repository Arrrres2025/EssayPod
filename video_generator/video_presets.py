from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoPreset:
    name: str
    width: int
    height: int
    subtitle_font_size: int
    subtitle_margin_v: int
    subtitle_margin_lr: int
    subtitle_fade_ms: int = 0
    first_subtitle_fade_ms: int = 280
    background_color: str = "white"

    @property
    def aspect_label(self) -> str:
        return f"{self.width}x{self.height}"


PRESETS: dict[str, VideoPreset] = {
    "16:9": VideoPreset(
        name="16:9",
        width=1920,
        height=1080,
        subtitle_font_size=54,
        subtitle_margin_v=60,
        subtitle_margin_lr=60,
    ),
    "9:16": VideoPreset(
        name="9:16",
        width=1080,
        height=1920,
        subtitle_font_size=60,
        subtitle_margin_v=84,
        subtitle_margin_lr=36,
    ),
}


def get_video_preset(format_key: str) -> VideoPreset:
    key = format_key.strip()
    if key not in PRESETS:
        supported = ", ".join(PRESETS)
        raise ValueError(f"Unknown video format {format_key!r}; supported: {supported}")
    return PRESETS[key]
