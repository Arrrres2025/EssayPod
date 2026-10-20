from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OcrBlock:
    type: str
    page_index: int
    page_number: int
    bbox_pdf: tuple[float, float, float, float]
    bbox_ocr: tuple[float, float, float, float] | None = None
    bbox_normalized: tuple[float, float, float, float] | None = None
    confidence: float | None = None
    source: str = "remote_layout_ocr"
    block_id: str = ""
    image_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["bbox_pdf"] = list(self.bbox_pdf)
        if self.bbox_ocr is not None:
            payload["bbox_ocr"] = list(self.bbox_ocr)
        if self.bbox_normalized is not None:
            payload["bbox_normalized"] = list(self.bbox_normalized)
        return payload
