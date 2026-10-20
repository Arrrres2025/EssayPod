from __future__ import annotations

import subprocess
from pathlib import Path

BUBBLE_SOUND_PATH = Path(__file__).resolve().parent / "data" / "bubble_sound.mp3"
DEFAULT_BUBBLE_VOLUME = 0.85


def mix_key_point_bubbles(
    podcast_path: Path,
    timestamps: list[float],
    output_path: Path,
    *,
    bubble_path: Path = BUBBLE_SOUND_PATH,
    bubble_volume: float = DEFAULT_BUBBLE_VOLUME,
) -> Path | None:
    if not timestamps:
        return None
    if not podcast_path.is_file():
        raise FileNotFoundError(f"Podcast audio not found: {podcast_path}")
    if not bubble_path.is_file():
        print(f"[audio] bubble sound not found: {bubble_path}", flush=True)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_parts: list[str] = []
    bubble_labels: list[str] = []
    for index, timestamp in enumerate(timestamps):
        delay_ms = max(int(round(timestamp * 1000)), 0)
        label = f"b{index}"
        filter_parts.append(
            f"[1:a]aresample=24000,aformat=channel_layouts=mono,"
            f"volume={bubble_volume},adelay={delay_ms}|{delay_ms}[{label}]"
        )
        bubble_labels.append(f"[{label}]")

    inputs = len(bubble_labels) + 1
    filter_parts.append(
        # amix enables normalization by default. Delayed one-shot inputs end at
        # different times, so that normalization progressively raises the podcast
        # gain as earlier bubble tracks drop out. Keep the programme track at a
        # fixed gain and only limit momentary summed peaks.
        f"[0:a]{''.join(bubble_labels)}"
        f"amix=inputs={inputs}:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:attack=5:release=50[aout]"
    )
    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(podcast_path),
        "-i",
        str(bubble_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[aout]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    print(
        f"[audio] mixed {len(timestamps)} bubble cue(s) -> {output_path}",
        flush=True,
    )
    return output_path
