from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def generate_task_covers(task_dir: Path, work_dir: Path) -> tuple[Path, Path]:
    output_dir = work_dir / "covers"
    output_dir.mkdir(parents=True, exist_ok=True)
    renderer = Path(__file__).with_name("render_demo.py")
    subprocess.run(
        [
            sys.executable,
            str(renderer),
            "--task-dir", str(task_dir),
            "--work-dir", str(work_dir),
            "--output-dir", str(output_dir),
        ],
        check=True,
    )
    portrait = output_dir / "mcp_guard_cover_3x4.png"
    landscape = output_dir / "mcp_guard_cover_16x9.png"
    if not portrait.is_file() or not landscape.is_file():
        raise RuntimeError("Cover renderer did not produce both cover ratios")
    return landscape, portrait


def prepend_cover_to_video(video_path: Path, cover_path: Path, *, duration: float = 1.5) -> None:
    """Add a silent cover intro without dropping or overlapping the original audio."""
    temp_path = video_path.with_name(f"{video_path.stem}.with_cover.tmp.mp4")
    filter_complex = (
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,settb=1/25,"
        f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[introv];"
        f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[introa];"
        "[1:v]scale=1920:1080,setsar=1,fps=25,settb=1/25,setpts=PTS-STARTPTS[mainv];"
        "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[maina];"
        "[introv][introa][mainv][maina]concat=n=2:v=1:a=1[vcat][a];"
        "[vcat]fps=25,settb=1/25,setpts=N/(25*TB)[v]"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(cover_path), "-i", str(video_path),
            "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temp_path),
        ],
        check=True,
    )
    temp_path.replace(video_path)
