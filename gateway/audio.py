"""Audio pipeline interface — visual generation never depends on TTS availability."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class TTSAdapter(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def synthesize(self, text: str, output: Path, *, voice: str | None = None) -> Path:
        ...


class NullTTSAdapter(TTSAdapter):
    """Placeholder when no local/open TTS is installed."""

    name = "null"

    def available(self) -> bool:
        return False

    def synthesize(self, text: str, output: Path, *, voice: str | None = None) -> Path:
        raise RuntimeError("No TTS adapter available")


class AudioPipeline:
    def __init__(self, tts: TTSAdapter | None = None) -> None:
        self.tts = tts or NullTTSAdapter()

    def build_voiceover_track(
        self,
        segments: list[dict[str, Any]],
        output: Path,
    ) -> Path | None:
        """segments: [{text, start, duration}] — returns mixed voiceover or None."""
        if not self.tts.available():
            return None
        clips: list[Path] = []
        tmp = output.parent / "_tts"
        tmp.mkdir(parents=True, exist_ok=True)
        for i, seg in enumerate(segments):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            clip = tmp / f"vo_{i:03d}.wav"
            try:
                self.tts.synthesize(text, clip)
                clips.append(clip)
            except Exception:
                continue
        if not clips:
            return None
        return mix_audio_clips(clips, output)

    def mix(
        self,
        video: Path,
        output: Path,
        *,
        voiceover: Path | None = None,
        music: Path | None = None,
        sfx: Path | None = None,
        music_volume: float = 0.18,
    ) -> Path:
        return mix_into_video(
            video,
            output,
            voiceover=voiceover,
            music=music,
            sfx=sfx,
            music_volume=music_volume,
        )


def mix_audio_clips(clips: list[Path], output: Path) -> Path:
    list_file = output.with_suffix(".txt")
    list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        "aac",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"audio concat failed: {proc.stderr[-400:]}")
    return output


def mix_into_video(
    video: Path,
    output: Path,
    *,
    voiceover: Path | None = None,
    music: Path | None = None,
    sfx: Path | None = None,
    music_volume: float = 0.18,
) -> Path:
    """Mux optional audio under the video. Never invents fake narration."""
    inputs = ["-i", str(video)]
    filter_parts: list[str] = []
    maps = ["-map", "0:v"]
    audio_inputs = []
    idx = 1
    if voiceover and voiceover.exists():
        inputs.extend(["-i", str(voiceover)])
        audio_inputs.append(idx)
        idx += 1
    if music and music.exists():
        inputs.extend(["-i", str(music)])
        audio_inputs.append(idx)
        idx += 1
    if sfx and sfx.exists():
        inputs.extend(["-i", str(sfx)])
        audio_inputs.append(idx)
        idx += 1

    if not audio_inputs:
        # Keep silent video intact.
        output.parent.mkdir(parents=True, exist_ok=True)
        if video.resolve() != output.resolve():
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(video),
                "-c",
                "copy",
                str(output),
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=False)
            if not output.exists():
                output.write_bytes(video.read_bytes())
        return output

    # Simple amix of available tracks.
    n = len(audio_inputs)
    weights = []
    for i, aidx in enumerate(audio_inputs):
        # First track assumed voiceover; subsequent quieter.
        vol = 1.0 if i == 0 else music_volume
        filter_parts.append(f"[{aidx}:a]volume={vol}[a{i}]")
        weights.append(f"[a{i}]")
    filter_parts.append(
        f"{''.join(weights)}amix=inputs={n}:duration=first:dropout_transition=2[aout]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"audio mix failed: {proc.stderr[-600:]}")
    return output
