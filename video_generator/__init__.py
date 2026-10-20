"""EssayPod video generator: PDF pages + Whisper subtitles + ffmpeg compose."""

from .pipeline import generate_video

__all__ = ["generate_video"]
