"""Instagram channel — publishes to a real Business/Creator account.

Uses the Instagram Graph API's documented two-step publish: create a media
container pointing at a publicly reachable image URL, then publish it.

What this genuinely requires (there is no way around it, and no library can
fake it): an Instagram **Business or Creator** account linked to a Facebook
Page, a Meta app with ``instagram_content_publish`` permission, a long-lived
access token, and image URLs Meta's servers can fetch — Instagram pulls the
image itself, so a local file path will not work.

Set INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN to switch it on.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramChannel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.user_id = cfg.instagram_user_id
        self.token = cfg.instagram_access_token

    @property
    def enabled(self) -> bool:
        return bool(self.user_id and self.token)

    def _post(self, path: str, params: dict) -> dict:
        data = urllib.parse.urlencode({**params, "access_token": self.token}).encode()
        req = urllib.request.Request(f"{GRAPH}/{path}", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    def publish_image(self, image_url: str, caption: str) -> str:
        """Publish a post. Returns the media id.

        ``image_url`` must be publicly reachable — Instagram fetches it from
        its own servers, so host it somewhere with a real URL first.
        """
        if not self.enabled:
            raise RuntimeError("Instagram channel is not configured")
        container = self._post(
            f"{self.user_id}/media", {"image_url": image_url, "caption": caption[:2200]}
        )
        published = self._post(
            f"{self.user_id}/media_publish", {"creation_id": container["id"]}
        )
        return published["id"]

    def publish_reel(self, video_url: str, caption: str, *, cover_url: str | None = None) -> str:
        """Publish a Reel. ``video_url`` must be a public MP4 (H.264/AAC).

        Video containers are processed asynchronously, so this polls until Meta
        reports the upload finished before publishing.
        """
        import time

        if not self.enabled:
            raise RuntimeError("Instagram channel is not configured")
        params = {"media_type": "REELS", "video_url": video_url, "caption": caption[:2200]}
        if cover_url:
            params["cover_url"] = cover_url
        container = self._post(f"{self.user_id}/media", params)

        for _ in range(30):  # up to ~5 min; Meta's own guidance for video processing
            status = self._get(container["id"], {"fields": "status_code"})
            if status.get("status_code") == "FINISHED":
                break
            if status.get("status_code") == "ERROR":
                raise RuntimeError(f"Instagram rejected the video: {status}")
            time.sleep(10)

        return self._post(f"{self.user_id}/media_publish", {"creation_id": container["id"]})["id"]

    # ---------------------------------------------------------------- engagement

    def recent_comments(self, limit: int = 25) -> list[dict]:
        """Comments on recent media — real people to reply to."""
        if not self.enabled:
            return []
        media = self._get(f"{self.user_id}/media", {"fields": "id,caption", "limit": 10})
        out = []
        for item in media.get("data", []):
            comments = self._get(
                f"{item['id']}/comments",
                {"fields": "id,text,username,timestamp,replies", "limit": limit},
            )
            for comment in comments.get("data", []):
                comment["media_id"] = item["id"]
                comment["media_caption"] = item.get("caption", "")
                out.append(comment)
        return out

    def reply_to_comment(self, comment_id: str, message: str) -> str:
        return self._post(f"{comment_id}/replies", {"message": message[:2200]})["id"]

    def conversations(self, limit: int = 20) -> list[dict]:
        """Instagram DM threads (requires instagram_manage_messages)."""
        if not self.enabled:
            return []
        data = self._get(
            f"{self.user_id}/conversations",
            {"platform": "instagram", "fields": "id,participants,messages{id,message,from,created_time}",
             "limit": limit},
        )
        return data.get("data", [])

    def send_dm(self, recipient_id: str, message: str) -> dict:
        return self._post(
            f"{self.user_id}/messages",
            {"recipient": json.dumps({"id": recipient_id}), "message": json.dumps({"text": message})},
        )

    # --------------------------------------------------------------------- ads

    def create_ad_campaign(
        self,
        *,
        ad_account_id: str,
        name: str,
        daily_budget_cents: int,
        objective: str = "OUTCOME_TRAFFIC",
        status: str = "PAUSED",
    ) -> dict:
        """Create a campaign on the Marketing API.

        Defaults to PAUSED on purpose: an agent should never be able to start
        spending ad money without a human flipping it to ACTIVE.
        """
        if not self.enabled:
            raise RuntimeError("Instagram channel is not configured")
        account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        return self._post(
            f"{account}/campaigns",
            {
                "name": name,
                "objective": objective,
                "status": status,
                "daily_budget": daily_budget_cents,
                "special_ad_categories": "[]",
            },
        )

    def campaign_spend(self, campaign_id: str) -> dict:
        """Real spend and results, so ad ROI can be checked against revenue."""
        return self._get(
            f"{campaign_id}/insights",
            {"fields": "spend,impressions,clicks,cpc,actions", "date_preset": "maximum"},
        )

    def _get(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode({**params, "access_token": self.token})
        with urllib.request.urlopen(f"{GRAPH}/{path}?{query}", timeout=60) as resp:
            return json.loads(resp.read())

    def insights(self) -> dict:
        """Real reach numbers, so marketing claims can be checked."""
        if not self.enabled:
            return {}
        url = (
            f"{GRAPH}/{self.user_id}/insights?metric=reach,profile_views,follower_count"
            f"&period=day&access_token={self.token}"
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
