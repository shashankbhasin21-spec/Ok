"""Claude client wrapper: the part of an agent that actually does the work.

Adds three things the raw SDK leaves to the caller and this framework needs:
per-call cost in cents (so margin is measurable), refusal handling, and
optional JSON-schema-constrained output for the structured steps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# USD per million tokens (input, output).
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}


class Refused(RuntimeError):
    """Claude's safety classifiers declined the request."""


class NotConfigured(RuntimeError):
    """No Claude credential resolved — the agents cannot think without one."""


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    cost_cents: int
    model: str

    def json(self) -> Any:
        return json.loads(self.text)


def cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    """Cost of a call, rounded up to the cent — never under-report spend."""
    in_price, out_price = PRICES.get(model, PRICES["claude-opus-5"])
    dollars = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    cents = dollars * 100
    return int(cents) + (1 if cents % 1 else 0)


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg
        import anthropic  # lazy: importing earner shouldn't require credentials

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        research: bool = False,
        max_searches: int = 8,
    ) -> Completion:
        """One Claude call. Streams so long outputs can't hit an HTTP timeout.

        ``research=True`` turns on Claude's server-side web search and fetch, so
        the work is grounded in current sources rather than model memory —
        which is the difference between a deliverable a client accepts and one
        they catch out on a stale fact.
        """
        params: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort or self.cfg.effort},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            # Stable prefix first, cached — the system prompt is identical across
            # every job an agent runs, so this is the cheap half of every call.
            params["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if schema:
            params["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        if research:
            # Structured output and server tools are mutually exclusive in
            # practice: search results arrive as their own blocks.
            params["tools"] = [
                {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
                {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_searches},
            ]

        message = self._stream(params)
        # A long research turn can pause after the server-tool iteration cap;
        # resume it rather than returning a half-finished deliverable.
        rounds = 0
        while message.stop_reason == "pause_turn" and rounds < 5:
            params["messages"] = [
                *params["messages"],
                {"role": "assistant", "content": message.content},
            ]
            message = self._stream(params)
            rounds += 1

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise Refused(f"declined ({getattr(details, 'category', 'unspecified')})")

        text = "".join(b.text for b in message.content if b.type == "text")
        usage = message.usage
        searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
        billed_in = usage.input_tokens + getattr(usage, "cache_creation_input_tokens", 0) or 0
        return Completion(
            text=text,
            input_tokens=billed_in,
            output_tokens=usage.output_tokens,
            cost_cents=cost_cents(message.model, billed_in, usage.output_tokens)
            + searches,  # web search bills $10/1k requests = 1¢ each
            model=message.model,
        )

    def _stream(self, params: dict):
        """Request with server-side refusal fallback, degrading if unsupported."""
        try:
            return self._attempt(params)
        except TypeError as exc:
            if "authentication" not in str(exc).lower():
                raise
            raise NotConfigured(
                "No Claude credential found. Set ANTHROPIC_API_KEY, or run `ant auth login`."
            ) from exc

    def _attempt(self, params: dict):
        try:
            with self.client.beta.messages.stream(
                **params,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                return stream.get_final_message()
        except self._anthropic.BadRequestError as exc:
            if "fallback" not in str(exc).lower():
                raise
            with self.client.messages.stream(**params) as stream:
                return stream.get_final_message()
