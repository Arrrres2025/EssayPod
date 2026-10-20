from __future__ import annotations

BASE_CANVAS_HEIGHT = 720


def ui_scale(canvas_height: int) -> float:
    return canvas_height / BASE_CANVAS_HEIGHT


def scale_ui(value: int | float, canvas_height: int) -> int:
    return max(1, int(round(float(value) * ui_scale(canvas_height))))


def compute_scaled_page_size(
    page_width: int,
    page_height: int,
    canvas_width: int,
    canvas_height: int,
) -> tuple[int, int]:
    """Return page dimensions after scale+decrease (before pad) on the canvas."""
    if page_width <= 0 or page_height <= 0:
        raise ValueError(f"Invalid page size: {page_width}x{page_height}")
    scale = min(canvas_width / page_width, canvas_height / page_height)
    scaled_w = int(round(page_width * scale))
    scaled_h = int(round(page_height * scale))
    return scaled_w, scaled_h


def compute_left_gutter(
    page_width: int,
    page_height: int,
    canvas_width: int,
    canvas_height: int,
) -> int:
    """Width of the left white margin when the page is centered on the canvas."""
    scaled_w, _ = compute_scaled_page_size(page_width, page_height, canvas_width, canvas_height)
    return max((canvas_width - scaled_w) // 2, 0)


def compute_right_gutter(
    page_width: int,
    page_height: int,
    canvas_width: int,
    canvas_height: int,
) -> int:
    scaled_w, _ = compute_scaled_page_size(page_width, page_height, canvas_width, canvas_height)
    return max(canvas_width - scaled_w - compute_left_gutter(page_width, page_height, canvas_width, canvas_height), 0)


def compute_pdf_page_bleed_px(
    page_width: int,
    page_height: int,
    canvas_width: int,
    canvas_height: int,
    *,
    bleed_ratio: float = 0.12,
) -> int:
    """
    Extra horizontal space inside the PDF page image that is blank margin.

    Board text may extend into this zone (user confirmed PDF has internal white border).
    """
    scaled_w, _ = compute_scaled_page_size(page_width, page_height, canvas_width, canvas_height)
    return max(int(scaled_w * bleed_ratio), 0)
