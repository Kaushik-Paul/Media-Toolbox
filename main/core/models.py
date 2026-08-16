"""Typed models for media info and job manifests."""
from __future__ import annotations

from pydantic import BaseModel, Field


class StreamInfo(BaseModel):
    index: int = 0
    codec_type: str = ""  # video | audio | subtitle | other
    codec_name: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bit_rate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    pix_fmt: str | None = None
    language: str | None = None
    duration: float | None = None


class MediaInfo(BaseModel):
    filename: str = ""
    size: int = 0
    duration_seconds: float | None = None
    format_name: str = ""
    bit_rate: int | None = None
    streams: list[StreamInfo] = Field(default_factory=list)

    def streams_of(self, codec_type: str) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == codec_type]

    @property
    def has_video(self) -> bool:
        return any(s.codec_type == "video" for s in self.streams)

    @property
    def has_audio(self) -> bool:
        return any(s.codec_type == "audio" for s in self.streams)

    @property
    def primary_video(self) -> StreamInfo | None:
        videos = self.streams_of("video")
        return videos[0] if videos else None

    @property
    def primary_audio(self) -> StreamInfo | None:
        audios = self.streams_of("audio")
        return audios[0] if audios else None

    @property
    def subtitle_streams(self) -> list[StreamInfo]:
        return self.streams_of("subtitle")

    def summary_dict(self) -> dict:
        info: dict = {"duration_seconds": self.duration_seconds}
        v = self.primary_video
        if v:
            info.update({"width": v.width, "height": v.height, "video_codec": v.codec_name, "fps": v.fps})
        a = self.primary_audio
        if a:
            info.update({"audio_codec": a.codec_name, "sample_rate": a.sample_rate, "channels": a.channels})
        return info


class OutputFile(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int


class JobManifest(BaseModel):
    schema_version: int = 1
    job_id: str
    source: str = "cpu"
    operation: str
    original_filename: str = ""
    original_size: int = 0
    created_at: str = ""
    completed_at: str = ""
    expires_at: str = ""
    expires_unix: int = 0
    outputs: list[OutputFile] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    media_info: dict = Field(default_factory=dict)
    app_version: str = ""
