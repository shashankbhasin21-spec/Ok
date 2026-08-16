"""Rendered media is real files, and autopilot stops itself."""

from __future__ import annotations

from PIL import Image

from earner.media import REEL_SIZE, Scene, VideoSpec, render_video
from earner.platform import Platform


def test_reel_frames_render_and_text_fits(tmp_path):
    spec = VideoSpec(
        title="t",
        scenes=[
            Scene("A hook that has to earn the next two seconds.", 0.2, True),
            Scene("Supporting line.", 0.2),
        ],
        fps=5,
    )
    result = render_video(spec, tmp_path / "out")

    assert result["frames"] > 0
    poster = Image.open(result["poster"])
    assert poster.size == REEL_SIZE

    # Nothing may be drawn into the outer 7% margin on either side.
    pixels = poster.convert("RGB")
    margin = int(REEL_SIZE[0] * 0.07)
    background = pixels.getpixel((2, 2))
    for y in range(0, REEL_SIZE[1], 17):
        assert pixels.getpixel((margin // 2, y)) == background
        assert pixels.getpixel((REEL_SIZE[0] - margin // 2, y)) == background


def test_autopilot_stops_on_target_hit(cfg):
    platform = Platform(cfg, gate_name="hold")
    try:
        platform.set_target(10_000, hours=50)
        job = platform.ledger.create_job("delivery", "Paid work", 10_000, "usd")
        invoice = platform.ledger.record_invoice(
            job.id, "sandbox", "ref", 10_000, "usd", "open", None
        )
        platform.ledger.settle(invoice, "evt", 10_000, "usd")

        outcome = platform.autopilot(interval=0, max_cycles=5)
        assert outcome["stopped"] == "target_hit"
        assert outcome["cycles"] == 1, "it must stop the moment the target is met"
    finally:
        platform.close()


def test_autopilot_stops_when_spend_outruns_revenue(cfg):
    platform = Platform(cfg, gate_name="hold")
    try:
        platform.set_target(500_000, hours=50)
        job = platform.ledger.create_job("delivery", "Burned tokens", 0, "usd")
        platform.ledger.add_cost(job.id, 5_000)  # $50 spent, nothing earned

        outcome = platform.autopilot(interval=0, max_cycles=5)
        assert outcome["stopped"] == "spend_guard"
    finally:
        platform.close()
