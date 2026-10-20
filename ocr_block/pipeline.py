from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

from .crop import crop_blocks_from_pdf
from .env import bootstrap_env, get_paddle_ocr_base_url
from .hybrid import extract_direct_bitmaps, plan_hybrid_pdf_extraction, write_page_subset
from .layout_parse import DEFAULT_IMAGE_TYPES, extract_layout_blocks
from .models import OcrBlock
from .remote_layout_ocr import ocr_pdf


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _figure_manifest_items(blocks: list[OcrBlock], *, ready: bool) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for block in blocks:
        base = str(block.meta.get("figure_id") or f"figure_page_{block.page_number}")
        totals[base] = totals.get(base, 0) + 1
    counters: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for block in blocks:
        base = str(block.meta.get("figure_id") or f"figure_page_{block.page_number}")
        counters[base] = counters.get(base, 0) + 1
        figure_id = base
        if totals[base] > 1:
            figure_id = f"{base}_{chr(96 + counters[base])}"
        items.append(
            {
                "figure_id": figure_id,
                "label": str(block.meta.get("label") or figure_id.replace("_", " ").title()),
                "caption": str(block.meta.get("caption") or ""),
                "page_number": block.page_number,
                "type": block.type,
                "source": block.source,
                "ready": ready and bool(block.image_path),
                "image_path": block.image_path if ready else None,
            }
        )
    return items


def run_ocr_image_extraction(
    *,
    pdf_path: Path,
    out_dir: Path,
    dpi: int = 150,
    mode: str = "accurate",
    device: str = "gpu",
    poll_interval: float = 3.0,
    timeout_seconds: float = 1800,
    image_types: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Call remote Paddle Layout OCR, crop image-type blocks, save paths + timing."""
    bootstrap_env()
    base_url = get_paddle_ocr_base_url()
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    log_path = out_dir / "ocr_run.log"

    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    started = time.perf_counter()
    timing: dict[str, float | int | str] = {
        "pdf_path": str(pdf_path),
        "out_dir": str(out_dir),
        "ocr_base_url": base_url,
        "mode": mode,
        "device": device,
    }
    log_lines = [f"[ocr_block] start remote OCR pdf={pdf_path} base={base_url}"]

    submit_started = time.perf_counter()
    submit_response, ocr_result = ocr_pdf(
        base_url,
        pdf_path,
        mode=mode,
        device=device,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )
    timing["remote_ocr_sec"] = round(time.perf_counter() - submit_started, 3)
    timing["page_count"] = len(ocr_result.get("pages") or [])
    timing["job_id"] = str(submit_response.get("job_id") or "")
    log_lines.append(
        f"[ocr_block] remote OCR job={timing['job_id']} pages={timing['page_count']} "
        f"elapsed={timing['remote_ocr_sec']}s"
    )

    _write_json(out_dir / "ocr_job_submit.json", submit_response)
    _write_json(out_dir / "layout_ocr_result.json", ocr_result)

    parse_started = time.perf_counter()
    image_types = image_types or DEFAULT_IMAGE_TYPES
    image_blocks, blocks_by_type = extract_layout_blocks(ocr_result, image_types=image_types)
    timing["parse_sec"] = round(time.perf_counter() - parse_started, 3)
    timing["layout_block_types"] = sorted(blocks_by_type.keys())
    timing["image_type_targets"] = sorted(image_types)
    timing["image_blocks"] = len(image_blocks)
    log_lines.append(
        f"[ocr_block] layout types={timing['layout_block_types']} "
        f"image_blocks={len(image_blocks)} parse={timing['parse_sec']}s"
    )

    _write_json(
        out_dir / "layout_by_type.json",
        {
            "blocks_by_type": blocks_by_type,
            "image_type_targets": sorted(image_types),
        },
    )

    crop_started = time.perf_counter()
    cropped = crop_blocks_from_pdf(pdf_path, image_blocks, images_dir, dpi=dpi)
    timing["crop_sec"] = round(time.perf_counter() - crop_started, 3)
    timing["cropped_images"] = len(cropped)
    log_lines.append(
        f"[ocr_block] cropped_images={len(cropped)} elapsed={timing['crop_sec']}s"
    )

    timing["total_sec"] = round(time.perf_counter() - started, 3)
    log_lines.append(f"[ocr_block] done total={timing['total_sec']}s")

    payload = {
        "timing": timing,
        "images": [block.to_dict() for block in cropped],
        "image_blocks": [block.to_dict() for block in image_blocks],
        "blocks_by_type": blocks_by_type,
    }
    _write_json(out_dir / "ocr_blocks.json", payload)
    _write_json(out_dir / "timing.json", timing)
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return payload


def run_ocr_for_task(
    task_dir: Path,
    *,
    dpi: int = 150,
    mode: str = "fast",
    device: str = "gpu",
) -> dict[str, Any]:
    pdf_path = task_dir / "input.pdf"
    out_dir = task_dir / "ocr_block"
    status_path = out_dir / "status.json"

    _write_json(
        status_path,
        {"status": "running", "started_at": time.time(), "pdf": str(pdf_path)},
    )
    try:
        payload = run_hybrid_ocr_for_task(
            pdf_path=pdf_path, out_dir=out_dir, dpi=dpi, mode=mode, device=device
        )
        _write_json(
            status_path,
            {
                "status": "ready",
                "finished_at": time.time(),
                "timing": payload["timing"],
                "image_count": len(payload["images"]),
            },
        )
        return payload
    except Exception as exc:
        figures_path = out_dir / "figures.json"
        if figures_path.is_file():
            try:
                figures = json.loads(figures_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                figures = {"figures": []}
            _write_json(figures_path, {**figures, "status": "failed", "error": str(exc)})
        _write_json(
            status_path,
            {
                "status": "failed",
                "finished_at": time.time(),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        error_log = out_dir / "ocr_run.log"
        error_log.parent.mkdir(parents=True, exist_ok=True)
        error_log.write_text(
            error_log.read_text(encoding="utf-8") + f"\n[ocr_block] failed: {exc}\n"
            if error_log.exists()
            else f"[ocr_block] failed: {exc}\n{traceback.format_exc()}\n",
            encoding="utf-8",
        )
        raise


def run_hybrid_ocr_for_task(
    *,
    pdf_path: Path,
    out_dir: Path,
    dpi: int = 150,
    mode: str = "fast",
    device: str = "gpu",
    poll_interval: float = 3.0,
    timeout_seconds: float = 1800,
) -> dict[str, Any]:
    """Extract standalone bitmaps directly; OCR only composite-figure pages."""
    bootstrap_env()
    base_url = get_paddle_ocr_base_url()
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    started = time.perf_counter()

    plan_started = time.perf_counter()
    plan = plan_hybrid_pdf_extraction(pdf_path)
    direct = extract_direct_bitmaps(pdf_path, plan.direct_images, images_dir, dpi=dpi)
    plan_sec = round(time.perf_counter() - plan_started, 3)

    pending: list[OcrBlock] = []
    for page_index in plan.ocr_page_indices:
        hints = plan.figure_hints.get(page_index) or [{}]
        for hint in hints:
            pending.append(
                OcrBlock(
                    type="figure",
                    page_index=page_index,
                    page_number=page_index + 1,
                    bbox_pdf=(0.0, 0.0, 1.0, 1.0),
                    source="remote_layout_ocr",
                    block_id=str(hint.get("figure_id") or f"figure_page_{page_index + 1}"),
                    meta=hint,
                )
            )
    _write_json(
        out_dir / "figures.json",
        {
            "status": "running",
            "figures": [
                *_figure_manifest_items(direct, ready=True),
                *_figure_manifest_items(pending, ready=False),
            ],
        },
    )

    submit_response: dict[str, Any] = {}
    ocr_result: dict[str, Any] = {"pages": []}
    remote_sec = 0.0
    remapped_blocks: list[OcrBlock] = []
    blocks_by_type: dict[str, list[dict[str, Any]]] = {}
    subset_path = out_dir / "ocr_candidate_pages.pdf"

    if plan.ocr_page_indices:
        write_page_subset(pdf_path, plan.ocr_page_indices, subset_path)
        remote_started = time.perf_counter()
        submit_response, ocr_result = ocr_pdf(
            base_url,
            subset_path,
            mode=mode,
            device=device,
            poll_interval=poll_interval,
            timeout_seconds=timeout_seconds,
        )
        remote_sec = round(time.perf_counter() - remote_started, 3)
        subset_blocks, subset_by_type = extract_layout_blocks(ocr_result)
        for block in subset_blocks:
            original_index = plan.ocr_page_indices[block.page_index]
            hint = (plan.figure_hints.get(original_index) or [{}])[0]
            remapped_blocks.append(
                OcrBlock(
                    type=block.type,
                    page_index=original_index,
                    page_number=original_index + 1,
                    bbox_pdf=block.bbox_pdf,
                    bbox_ocr=block.bbox_ocr,
                    confidence=block.confidence,
                    source=block.source,
                    block_id=f"p{original_index + 1:04d}_{block.type}_{len(remapped_blocks) + 1:02d}",
                    meta={
                        **block.meta,
                        **hint,
                        "ocr_subset_page_number": block.page_number,
                    },
                )
            )
        for block_type, records in subset_by_type.items():
            remapped: list[dict[str, Any]] = []
            for record in records:
                original_index = plan.ocr_page_indices[int(record["page_index"])]
                remapped.append(
                    {
                        **record,
                        "page_index": original_index,
                        "page_number": original_index + 1,
                        "ocr_subset_page_number": record["page_number"],
                    }
                )
            blocks_by_type[block_type] = remapped

    crop_started = time.perf_counter()
    cropped = crop_blocks_from_pdf(pdf_path, remapped_blocks, images_dir, dpi=dpi)
    crop_sec = round(time.perf_counter() - crop_started, 3)
    images = sorted([*direct, *cropped], key=lambda item: (item.page_index, item.bbox_pdf[1]))

    timing: dict[str, Any] = {
        "pdf_path": str(pdf_path),
        "out_dir": str(out_dir),
        "mode": mode,
        "device": device,
        "strategy": "direct_bitmap_then_candidate_page_ocr",
        "plan_sec": plan_sec,
        "direct_images": len(direct),
        "ocr_candidate_pages": len(plan.ocr_page_indices),
        "ocr_original_page_numbers": [index + 1 for index in plan.ocr_page_indices],
        "remote_ocr_sec": remote_sec,
        "ocr_image_blocks": len(remapped_blocks),
        "crop_sec": crop_sec,
        "cropped_images": len(cropped),
        "total_images": len(images),
        "total_sec": round(time.perf_counter() - started, 3),
        "job_id": str(submit_response.get("job_id") or ""),
    }
    payload = {
        "timing": timing,
        "images": [block.to_dict() for block in images],
        "direct_images": [block.to_dict() for block in direct],
        "image_blocks": [block.to_dict() for block in remapped_blocks],
        "blocks_by_type": blocks_by_type,
    }
    _write_json(out_dir / "ocr_job_submit.json", submit_response)
    _write_json(out_dir / "layout_ocr_result.json", ocr_result)
    _write_json(out_dir / "layout_by_type.json", {"blocks_by_type": blocks_by_type})
    _write_json(out_dir / "ocr_blocks.json", payload)
    _write_json(out_dir / "timing.json", timing)
    _write_json(
        out_dir / "figures.json",
        {"status": "ready", "figures": _figure_manifest_items(images, ready=True)},
    )
    (out_dir / "ocr_run.log").write_text(
        f"[ocr_block] hybrid direct={len(direct)} candidate_pages={timing['ocr_original_page_numbers']}\n"
        f"[ocr_block] remote={remote_sec}s cropped={len(cropped)} total={timing['total_sec']}s\n",
        encoding="utf-8",
    )
    return payload
