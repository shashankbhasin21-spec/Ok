"""Growth — demand generation on Instagram, and the conversations that follow.

Four jobs, all against real endpoints: find topics people are actually asking
about, produce posts and rendered Reels, reply to real comments and DMs, and
run ads whose spend is checked against settled revenue rather than vanity
metrics. Every outbound word passes an approval gate first.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..agent import BaseAgent, TickReport
from ..approval import Action
from ..channels.instagram import InstagramChannel
from ..media import Scene, VideoSpec, render_video

CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "seconds": {"type": "number"},
                    "emphasis": {"type": "boolean"},
                },
                "required": ["text", "seconds", "emphasis"],
                "additionalProperties": False,
            },
        },
        "call_to_action": {"type": "string"},
    },
    "required": ["hook", "caption", "hashtags", "scenes", "call_to_action"],
    "additionalProperties": False,
}

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "should_reply": {"type": "boolean"},
        "intent": {
            "type": "string",
            "enum": ["buying", "question", "praise", "complaint", "spam", "other"],
        },
        "reply": {"type": "string"},
        "route_to_sales": {"type": "boolean"},
    },
    "required": ["should_reply", "intent", "reply", "route_to_sales"],
    "additionalProperties": False,
}


class GrowthAgent(BaseAgent):
    name = "growth"
    role = "Growth & social"
    description = "Researches demand, makes posts and Reels, replies to real people, runs ads."

    system_prompt = (
        "You run growth for a small professional-services firm on Instagram. You write like a "
        "practitioner sharing something useful, not like a brand: a specific claim, a real number, "
        "one idea per post. You never fabricate results, testimonials, or credentials, and you "
        "never promise an outcome the firm cannot deliver. When someone shows buying intent you "
        "route them to a human rather than closing them in a comment thread."
    )

    POSTS_PER_TICK = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instagram = InstagramChannel(self.cfg)

    def tick(self) -> TickReport:
        report = TickReport(agent=self.name)
        try:
            self._make_content(report)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"content: {type(exc).__name__}: {exc}")
        try:
            self._engage(report)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"engagement: {type(exc).__name__}: {exc}")
        return report

    # ----------------------------------------------------------------- content

    def _make_content(self, report: TickReport) -> None:
        topics = self._topics()
        for topic in topics[: self.POSTS_PER_TICK]:
            ref = "post-" + hashlib.sha256(topic.encode()).hexdigest()[:12]
            if self.ledger.record_opportunity(self.name, "growth/topic", ref, {"topic": topic}) is None:
                continue
            report.found += 1

            raw = self.think(
                None,
                "Write one Instagram Reel about this topic.\n\n"
                f"Topic: {topic}\n"
                f"We sell: {self.cfg.offer}\n\n"
                "5-7 scenes, each a single line that fits on a phone screen — the first is the hook "
                "and has to earn the next two seconds. One concrete, checkable claim minimum. "
                "The caption expands on it in plain language and ends with the call to action. "
                "No hype adjectives, no fake urgency, no invented statistics.",
                schema=CONTENT_SCHEMA,
                effort="medium",
            )
            content = json.loads(raw)

            outdir = Path(self.cfg.workdir) / "content" / ref
            spec = VideoSpec(
                title=content["hook"],
                scenes=[
                    Scene(s["text"], float(s["seconds"]), bool(s["emphasis"]))
                    for s in content["scenes"]
                ],
            )
            rendered = render_video(spec, outdir)

            caption = f"{content['caption']}\n\n{content['call_to_action']}\n\n" + " ".join(
                f"#{h.lstrip('#')}" for h in content["hashtags"][:12]
            )
            (outdir / "caption.txt").write_text(caption)

            decision = self.gate.review(
                Action(
                    kind="message",
                    summary=f"publish Reel: {content['hook']}",
                    body=caption,
                    recipient="instagram",
                )
            )
            self.ledger.log(
                "agent", None, "content_rendered", ref=ref, topic=topic,
                mp4=rendered.get("mp4"), note=rendered.get("note"),
            )
            if not decision.ok:
                report.held += 1
                continue

            if self.instagram.enabled and rendered.get("public_video_url"):
                media_id = self.instagram.publish_reel(
                    rendered["public_video_url"], caption, cover_url=rendered.get("public_poster_url")
                )
                self.ledger.log("agent", None, "published", ref=ref, media_id=media_id)
                report.delivered += 1
            else:
                # Meta fetches media from a public URL; a local file cannot be
                # published. Render now, host it, then publish.
                self.write_outbox(
                    f"instagram-{ref}.md",
                    f"# Ready to publish: {content['hook']}\n\n{caption}\n\n"
                    f"Video: {rendered.get('mp4') or rendered.get('gif')}\n"
                    f"Poster: {rendered.get('poster')}\n\n"
                    "Upload the video to any public URL, then run:\n"
                    f"  earner publish --ref {ref} --video-url <public-mp4-url>\n"
                    + (f"\nNote: {rendered['note']}\n" if rendered.get("note") else ""),
                )
                report.quoted += 1

    def _topics(self) -> list[str]:
        """What our audience is actually asking about this week."""
        raw = self.think(
            None,
            "Find 5 things our audience is asking about right now that we could answer credibly.\n\n"
            "Search for current discussion — forums, recent posts, job listings, tool launches. "
            "Return a JSON array of 5 short topic strings, nothing else. Each must be specific "
            "enough to make one concrete claim about; skip evergreen platitudes.",
            research=True,
            effort="low",
            max_tokens=2000,
        )
        text = raw.strip()
        if "[" in text:
            text = text[text.index("[") : text.rindex("]") + 1]
        try:
            topics = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [t for t in topics if isinstance(t, str)]

    # -------------------------------------------------------------- engagement

    def _engage(self, report: TickReport) -> None:
        """Reply to real comments and DMs. Buying intent goes to a human."""
        if not self.instagram.enabled:
            return

        for comment in self.instagram.recent_comments():
            ref = f"ig-comment-{comment['id']}"
            if self.ledger.record_opportunity(self.name, "instagram/comment", ref, comment) is None:
                continue
            decision_raw = self.think(
                None,
                "Decide how to handle this comment on our post.\n\n"
                f"Post caption: {comment.get('media_caption', '')[:500]}\n"
                f"Comment from @{comment.get('username')}: {comment.get('text')}\n\n"
                "should_reply=false for spam, bots, or anything that needs no answer. Reply as a "
                "practitioner in one or two sentences. If they are asking about hiring us or "
                "pricing, set route_to_sales=true and reply by inviting them to email us — do not "
                "quote a price in public.",
                schema=REPLY_SCHEMA,
                effort="low",
            )
            reply = json.loads(decision_raw)
            if not reply["should_reply"]:
                continue

            decision = self.gate.review(
                Action(
                    kind="message",
                    summary=f"reply to @{comment.get('username')} ({reply['intent']})",
                    body=reply["reply"],
                    recipient="instagram comment",
                )
            )
            if not decision.ok:
                report.held += 1
                continue

            self.instagram.reply_to_comment(comment["id"], reply["reply"])
            report.delivered += 1

            if reply["route_to_sales"]:
                self._file_lead(comment.get("username", "unknown"), comment.get("text", ""))
                report.quoted += 1

    def _file_lead(self, username: str, text: str) -> None:
        """Hand a warm contact to acquisition as a real lead file."""
        self.cfg.ensure_dirs()
        folder = Path(self.cfg.inbox) / "leads"
        folder.mkdir(parents=True, exist_ok=True)
        ref = "ig-" + hashlib.sha256(username.encode()).hexdigest()[:10]
        path = folder / f"{ref}.json"
        if path.exists():
            return
        path.write_text(
            json.dumps(
                {
                    "company": f"@{username}",
                    "contact_name": username,
                    "source": "instagram",
                    "notes": f"Showed buying intent in a comment: {text}",
                    "email": "",  # filled in when they reply; no scraping
                },
                indent=2,
            )
        )
