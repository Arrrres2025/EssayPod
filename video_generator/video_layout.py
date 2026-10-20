from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .layout import compute_pdf_page_bleed_px, compute_scaled_page_size, scale_ui

# Baseline layout constants at 720p; scaled via scale_ui for other canvas heights.
_BASE_MAIN_TITLE_Y = 48
_BASE_MARGIN_LEFT = 30
_BASE_MARGIN_TOP = 132
_BASE_BOTTOM_MARGIN = 56

REGION_NAMES = ("title", "key_points", "page", "figures")
MIN_REGION_SIZE = 80


@dataclass(frozen=True)
class LayoutRegion:
    x: int
    y: int
    w: int
    h: int

    def clamp_to_canvas(self, canvas_width: int, canvas_height: int) -> LayoutRegion:
        x = max(0, min(self.x, canvas_width - MIN_REGION_SIZE))
        y = max(0, min(self.y, canvas_height - MIN_REGION_SIZE))
        w = max(MIN_REGION_SIZE, min(self.w, canvas_width - x))
        h = max(MIN_REGION_SIZE, min(self.h, canvas_height - y))
        return LayoutRegion(x=x, y=y, w=_even(w), h=_even(h))


@dataclass(frozen=True)
class VideoLayout:
    canvas_width: int
    canvas_height: int
    title: LayoutRegion
    key_points: LayoutRegion
    page: LayoutRegion
    figures: LayoutRegion
    subtitle_margin_v: int = 60
    subtitle_font_size: int = 54
    title_font_size: int = 42
    key_points_font_size: int = 36
    title_shadow: bool = True
    key_points_shadow: bool = True
    title_alignment: str = "left"
    visible_page_count: int = 0
    figure_display_duration: float = 3.0
    background_color: str = "#ebe8df"
    z_order: tuple[str, ...] = REGION_NAMES
    layout_source: str = "recommended"

    def region(self, name: str) -> LayoutRegion:
        return getattr(self, name)

    def clamp_all(self) -> VideoLayout:
        order = tuple(name for name in self.z_order if name in REGION_NAMES)
        order += tuple(name for name in REGION_NAMES if name not in order)
        return VideoLayout(
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            title=self.title.clamp_to_canvas(self.canvas_width, self.canvas_height),
            key_points=self.key_points.clamp_to_canvas(self.canvas_width, self.canvas_height),
            page=self.page.clamp_to_canvas(self.canvas_width, self.canvas_height),
            figures=self.figures.clamp_to_canvas(self.canvas_width, self.canvas_height),
            subtitle_margin_v=max(20, min(self.subtitle_margin_v, self.canvas_height - 40)),
            subtitle_font_size=max(28, min(self.subtitle_font_size, 76)),
            title_font_size=max(24, min(self.title_font_size, 88)),
            key_points_font_size=max(22, min(self.key_points_font_size, 64)),
            title_shadow=bool(self.title_shadow),
            key_points_shadow=bool(self.key_points_shadow),
            title_alignment=self.title_alignment if self.title_alignment in {"left", "center", "right"} else "left",
            visible_page_count=max(0, min(int(self.visible_page_count), 10000)),
            figure_display_duration=max(1.0, min(float(self.figure_display_duration), 12.0)),
            background_color=self.background_color if re.fullmatch(r"#[0-9a-fA-F]{6}", self.background_color) else "#ebe8df",
            z_order=order,
            layout_source=self.layout_source if self.layout_source in {"recommended", "template1", "template2", "template3", "custom"} else "custom",
        )


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def compute_default_layout(
    *,
    canvas_width: int,
    canvas_height: int,
    page_width: int,
    page_height: int,
) -> VideoLayout:
    """Derive the legacy implicit 16:9 layout as explicit regions."""
    if canvas_width == 1920 and canvas_height == 1080:
        return VideoLayout(
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            title=LayoutRegion(x=28, y=26, w=800, h=174),
            key_points=LayoutRegion(x=32, y=201, w=590, h=790),
            page=LayoutRegion(x=528, y=0, w=1392, h=1080),
            figures=LayoutRegion(x=595, y=199, w=1282, h=824),
            subtitle_margin_v=20,
            subtitle_font_size=54,
            title_font_size=50,
            key_points_font_size=40,
            title_shadow=True,
            key_points_shadow=True,
            title_alignment="left",
            visible_page_count=0,
            figure_display_duration=5.0,
            background_color="#ebe8df",
            z_order=("page", "key_points", "figures", "title"),
            layout_source="recommended",
        ).clamp_all()
    scaled_w, scaled_h = compute_scaled_page_size(
        page_width, page_height, canvas_width, canvas_height
    )
    page_x = max((canvas_width - scaled_w) // 2, 0)
    page_y = max((canvas_height - scaled_h) // 2, 0)

    margin_left = scale_ui(_BASE_MARGIN_LEFT, canvas_height)
    title_y = scale_ui(_BASE_MAIN_TITLE_Y, canvas_height)
    key_points_y = scale_ui(_BASE_MARGIN_TOP, canvas_height)
    bottom_margin = scale_ui(_BASE_BOTTOM_MARGIN, canvas_height)

    bleed = compute_pdf_page_bleed_px(
        page_width, page_height, canvas_width, canvas_height
    )
    kp_width = max(page_x + bleed - margin_left, MIN_REGION_SIZE)
    kp_height = max(canvas_height - key_points_y - bottom_margin, MIN_REGION_SIZE)

    title_h = max(key_points_y - title_y, MIN_REGION_SIZE)
    figures_x = page_x + scaled_w
    figures_w = max(canvas_width - figures_x, MIN_REGION_SIZE)

    return VideoLayout(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        title=LayoutRegion(x=margin_left, y=title_y, w=kp_width, h=title_h),
        key_points=LayoutRegion(x=margin_left, y=key_points_y, w=kp_width, h=kp_height),
        page=LayoutRegion(x=page_x, y=page_y, w=scaled_w, h=scaled_h),
        figures=LayoutRegion(x=figures_x, y=key_points_y, w=figures_w, h=kp_height),
        subtitle_margin_v=60,
        subtitle_font_size=54,
        title_font_size=42,
        key_points_font_size=36,
        title_shadow=True,
        key_points_shadow=True,
        title_alignment="left",
        visible_page_count=0,
        figure_display_duration=3.0,
        background_color="#ebe8df",
        z_order=REGION_NAMES,
        layout_source="recommended",
    ).clamp_all()


def layout_to_dict(layout: VideoLayout) -> dict:
    payload = {
        "canvas_width": layout.canvas_width,
        "canvas_height": layout.canvas_height,
        "regions": {
            name: asdict(layout.region(name))
            for name in REGION_NAMES
        },
        "subtitle": {
            "margin_v": layout.subtitle_margin_v,
            "font_size": layout.subtitle_font_size,
        },
        "typography": {
            "title_font_size": layout.title_font_size,
            "key_points_font_size": layout.key_points_font_size,
            "title_shadow": layout.title_shadow,
            "key_points_shadow": layout.key_points_shadow,
            "title_alignment": layout.title_alignment,
        },
        "background": {"color": layout.background_color},
        "pdf": {"visible_page_count": layout.visible_page_count},
        "figures": {"display_duration": layout.figure_display_duration},
        "z_order": list(layout.z_order),
        "layout_source": layout.layout_source,
    }
    return payload


def layout_from_dict(payload: dict) -> VideoLayout:
    canvas_width = int(payload["canvas_width"])
    canvas_height = int(payload["canvas_height"])
    regions = payload.get("regions") or payload
    subtitle = payload.get("subtitle") or {}
    typography = payload.get("typography") or {}
    background = payload.get("background") or {}
    pdf = payload.get("pdf") or {}
    figures_config = payload.get("figures") or {}
    missing = [name for name in REGION_NAMES if name not in regions]
    if missing:
        raise ValueError(f"Missing layout regions: {', '.join(missing)}")

    def _region(name: str) -> LayoutRegion:
        item = regions[name]
        return LayoutRegion(
            x=int(item["x"]),
            y=int(item["y"]),
            w=int(item["w"]),
            h=int(item["h"]),
        )

    return VideoLayout(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        title=_region("title"),
        key_points=_region("key_points"),
        page=_region("page"),
        figures=_region("figures"),
        subtitle_margin_v=int(subtitle.get("margin_v", 60)),
        subtitle_font_size=int(subtitle.get("font_size", 54)),
        title_font_size=int(typography.get("title_font_size", 42)),
        key_points_font_size=int(typography.get("key_points_font_size", 36)),
        title_shadow=bool(typography.get("title_shadow", True)),
        key_points_shadow=bool(typography.get("key_points_shadow", True)),
        title_alignment=str(typography.get("title_alignment", "left")),
        visible_page_count=int(pdf.get("visible_page_count", 0)),
        figure_display_duration=float(figures_config.get("display_duration", 3.0)),
        background_color=str(background.get("color", "#ebe8df")),
        z_order=tuple(payload.get("z_order") or REGION_NAMES),
        layout_source=str(payload.get("layout_source") or "custom"),
    ).clamp_all()


def write_layout_json(layout: VideoLayout, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(layout_to_dict(layout), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_layout_json(path: Path) -> VideoLayout:
    return layout_from_dict(json.loads(path.read_text(encoding="utf-8")))


def resolve_layout(
    *,
    layout_path: Path,
    canvas_width: int,
    canvas_height: int,
    page_width: int,
    page_height: int,
) -> VideoLayout:
    if layout_path.is_file():
        layout = load_layout_json(layout_path)
        if layout.canvas_width == canvas_width and layout.canvas_height == canvas_height:
            return layout.clamp_all()
        print(
            f"[layout] saved layout size {layout.canvas_width}x{layout.canvas_height} "
            f"!= preset {canvas_width}x{canvas_height}, recomputing default",
            flush=True,
        )
    layout = compute_default_layout(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        page_width=page_width,
        page_height=page_height,
    )
    write_layout_json(layout, layout_path)
    return layout
