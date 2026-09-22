from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List

import yt_dlp
from faster_whisper import WhisperModel

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.txt"
WORK = ROOT / "work"
OUTPUT = ROOT / "output"

MAX_URLS = 10
TARGET_CLIPS = 25
MIN_CLIP = 28.0
MAX_CLIP = 60.0
MAX_PER_VIDEO = 4

HOOK_WORDS = {
    "how", "why", "best", "secret", "mistake", "mistakes", "never", "always",
    "free", "important", "simple", "easy", "fast", "faster", "exactly",
    "here's", "here", "this", "you", "your", "imagine", "stop", "start",
    "before", "after", "instead", "truth", "problem", "solution", "step",
    "steps", "tips", "trick", "tricks", "hack", "hacks", "save", "saved",
}
VALUE_WORDS = {
    "ai", "tool", "tools", "workflow", "automate", "automation", "agent",
    "agents", "productivity", "result", "results", "time", "money", "business",
    "content", "youtube", "video", "videos", "prompt", "prompts", "because",
    "example", "examples", "means", "use", "using", "build", "create",
}
FILLER = {"um", "uh", "like", "basically", "actually", "literally", "you know"}


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    video_idx: int
    video_id: str
    title: str
    source_url: str
    source_path: str
    start: float
    end: float
    text: str
    score: float


def run(cmd: List[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def clean_name(value: str, max_len: int = 70) -> str:
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip().replace(" ", "_")
    return value[:max_len] or "clip"


def load_sources() -> List[str]:
    if not SOURCES_FILE.exists():
        raise SystemExit(f"Missing {SOURCES_FILE}")
    lines = [x.strip() for x in SOURCES_FILE.read_text(encoding="utf-8").splitlines()]
    rights = any(x.lower() == "# rights_confirmed=yes" for x in lines)
    if not rights:
        raise SystemExit(
            "Set '# RIGHTS_CONFIRMED=yes' in clipper_free/sources.txt. "
            "Only use videos you own, are licensed to reuse, or that are clearly public-domain/CC with reuse rights."
        )
    urls = [x for x in lines if x and not x.startswith("#")]
    if not urls:
        raise SystemExit("No source URLs found in clipper_free/sources.txt")
    if len(urls) > MAX_URLS:
        raise SystemExit(f"Maximum {MAX_URLS} URLs per run; found {len(urls)}")
    return urls


def download_video(url: str, idx: int) -> tuple[str, str, str]:
    outtmpl = str(WORK / f"{idx:02d}_%(id)s.%(ext)s")
    opts = {
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "retries": 3,
        "fragment_retries": 3,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = str(info.get("id") or f"video{idx}")
        title = str(info.get("title") or video_id)
        requested = info.get("requested_downloads") or []
        candidates = []
        if requested:
            candidates.extend([x.get("filepath") for x in requested if x.get("filepath")])
        prepared = ydl.prepare_filename(info)
        candidates += [prepared, str(Path(prepared).with_suffix(".mp4"))]
        existing = [str(p) for p in WORK.glob(f"{idx:02d}_{video_id}.*") if p.is_file()]
        candidates += existing
        path = next((p for p in candidates if p and Path(p).exists()), None)
        if not path:
            raise FileNotFoundError(f"Could not locate downloaded file for {url}")
        return video_id, title, path


def transcribe(model: WhisperModel, path: str) -> List[Segment]:
    seg_iter, info = model.transcribe(
        path,
        beam_size=1,
        vad_filter=True,
        word_timestamps=False,
        condition_on_previous_text=False,
    )
    segs = []
    for s in seg_iter:
        text = (s.text or "").strip()
        if text:
            segs.append(Segment(float(s.start), float(s.end), text))
    print(f"Transcribed {len(segs)} segments; language={getattr(info, 'language', '?')}")
    return segs


def score_text(text: str, duration: float) -> float:
    low = text.lower()
    words = re.findall(r"[a-zA-Z0-9']+", low)
    if not words or duration <= 0:
        return -999
    wc = len(words)
    unique_ratio = len(set(words)) / max(1, wc)
    hook = sum(1 for w in words if w in HOOK_WORDS)
    value = sum(1 for w in words if w in VALUE_WORDS)
    numbers = sum(1 for w in words if any(c.isdigit() for c in w))
    filler_hits = sum(low.count(x) for x in FILLER)
    density = wc / duration
    density_score = max(0.0, 1.0 - abs(density - 2.4) / 2.4)
    length_score = 1.0 - min(abs(duration - 45.0) / 45.0, 1.0)
    punctuation = low.count("?") * 0.7 + low.count("!") * 0.5
    direct = (low.count(" you ") + low.count(" your ")) * 0.25
    return (
        hook * 1.4
        + value * 1.0
        + numbers * 0.9
        + unique_ratio * 6.0
        + density_score * 5.0
        + length_score * 4.0
        + punctuation
        + direct
        - filler_hits * 0.6
    )


def make_candidates(video_idx: int, video_id: str, title: str, url: str, path: str, segs: List[Segment]) -> List[Candidate]:
    out: List[Candidate] = []
    if not segs:
        return out
    # Start a candidate every two transcript segments. Extend until at least MIN_CLIP,
    # and stop when the next segment would push beyond MAX_CLIP.
    for i in range(0, len(segs), 2):
        start = max(0.0, segs[i].start - 0.15)
        j = i
        texts = []
        end = start
        while j < len(segs):
            proposed_end = segs[j].end + 0.15
            if proposed_end - start > MAX_CLIP:
                break
            texts.append(segs[j].text)
            end = proposed_end
            if end - start >= MIN_CLIP:
                text = " ".join(texts).strip()
                score = score_text(text, end - start)
                out.append(
                    Candidate(
                        video_idx, video_id, title, url, path,
                        start, end, text, score
                    )
                )
                # keep extending a little to generate 35-60s alternatives
                if end - start >= 48:
                    break
            j += 1
    return out


def overlap_ratio(a: Candidate, b: Candidate) -> float:
    if a.video_id != b.video_id:
        return 0.0
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    if inter <= 0:
        return 0.0
    return inter / min(a.end - a.start, b.end - b.start)


def select_top(candidates: List[Candidate]) -> List[Candidate]:
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    chosen: List[Candidate] = []
    counts = defaultdict(int)
    for c in ranked:
        if counts[c.video_id] >= MAX_PER_VIDEO:
            continue
        if any(overlap_ratio(c, x) > 0.45 for x in chosen):
            continue
        chosen.append(c)
        counts[c.video_id] += 1
        if len(chosen) >= TARGET_CLIPS:
            break

    # If strict per-video cap prevented 25, fill with the best remaining non-overlapping clips.
    if len(chosen) < TARGET_CLIPS:
        for c in ranked:
            if c in chosen:
                continue
            if any(overlap_ratio(c, x) > 0.45 for x in chosen):
                continue
            chosen.append(c)
            if len(chosen) >= TARGET_CLIPS:
                break
    return chosen


def render_clip(c: Candidate, rank: int) -> Path:
    duration = c.end - c.start
    fname = f"{rank:02d}_{clean_name(c.title, 48)}_{c.video_id}.mp4"
    out = OUTPUT / fname
    # Blurred 9:16 background + centered original frame. 720x1280 keeps artifacts compact.
    vf = (
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        "crop=720:1280,boxblur=18:5[bg];"
        "[0:v]scale=720:1280:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{c.start:.3f}",
        "-i", c.source_path,
        "-t", f"{duration:.3f}",
        "-filter_complex", vf,
        "-map", "[v]" if False else "0:v?",
    ]
    # Simpler robust invocation using -filter_complex and default filtered output label.
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{c.start:.3f}",
        "-i", c.source_path,
        "-t", f"{duration:.3f}",
        "-filter_complex",
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=18:5[bg];"
        "[0:v]scale=720:1280:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]",
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
    return out


def suggested_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return "AI Productivity Short"
    sentence = re.split(r"(?<=[.!?])\s+", text)[0]
    words = sentence.split()
    title = " ".join(words[:11]).strip(" .,!?:;-")
    if len(title) < 20:
        title = " ".join(text.split()[:14]).strip(" .,!?:;-")
    return title[:90]


def main() -> None:
    urls = load_sources()
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(OUTPUT, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    model_name = os.getenv("WHISPER_MODEL", "base.en")
    print(f"Loading Whisper model: {model_name}")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    all_candidates: List[Candidate] = []
    source_meta = []

    for idx, url in enumerate(urls, 1):
        print(f"\n=== SOURCE {idx}/{len(urls)} ===\n{url}")
        video_id, title, path = download_video(url, idx)
        segs = transcribe(model, path)
        candidates = make_candidates(idx, video_id, title, url, path, segs)
        print(f"Generated {len(candidates)} candidate clips")
        all_candidates.extend(candidates)
        source_meta.append({"index": idx, "video_id": video_id, "title": title, "url": url, "path": path})

    if not all_candidates:
        raise SystemExit("No viable clip candidates were generated.")

    selected = select_top(all_candidates)
    print(f"\nSelected {len(selected)} clips")

    manifest_rows = []
    for rank, c in enumerate(selected, 1):
        out = render_clip(c, rank)
        title = suggested_title(c.text)
        manifest_rows.append({
            "rank": rank,
            "source_title": c.title,
            "source_url": c.source_url,
            "source_video_id": c.video_id,
            "start_seconds": round(c.start, 2),
            "end_seconds": round(c.end, 2),
            "duration_seconds": round(c.end - c.start, 2),
            "score": round(c.score, 3),
            "suggested_title": title,
            "transcript_excerpt": c.text[:800],
            "file": out.name,
        })

    (OUTPUT / "manifest.json").write_text(json.dumps(manifest_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    with (OUTPUT / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)

    (OUTPUT / "sources.json").write_text(json.dumps(source_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone. Output: {OUTPUT}")


if __name__ == "__main__":
    main()
