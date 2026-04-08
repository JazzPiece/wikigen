"""
llm/claude_api.py — Anthropic Claude API backend.

Features:
- Disk-level LLM response cache (no re-calls for unchanged content)
- Sliding-window rate limiter
- Cost tracker with configurable per-run budget guard
"""

from __future__ import annotations

import hashlib
import time
from collections import deque

from ..config import WikiConfig
from .base import CostGuardError, LLMBackend, LLMResponse


# Prices per 1M tokens (USD). Update as Anthropic adjusts pricing.
_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-6":    {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":  {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":   {"input":  0.80, "output":  4.00},
    # Fallback for unknown models
    "default":            {"input":  3.00, "output": 15.00},
}


def _price(model: str) -> dict[str, float]:
    for key in _PRICES:
        if key in model:
            return _PRICES[key]
    return _PRICES["default"]


class ClaudeAPIBackend(LLMBackend):
    def __init__(self, cfg: WikiConfig) -> None:
        self._cfg = cfg
        self._model = cfg.llm.model
        self._max_tokens = cfg.llm.max_tokens_per_call
        self._api_key = cfg.llm.resolve_api_key()
        self._client = None  # Lazy-initialised

        # In-memory cache (keyed by sha256 of system+user prompt)
        self._prompt_cache: dict[str, LLMResponse] = {}

        # Rate limiting: sliding deque of (timestamp, tokens) tuples
        self._rpm = cfg.llm.rate_limit.requests_per_minute
        self._tpm = cfg.llm.rate_limit.tokens_per_minute
        self._window: deque[tuple[float, int]] = deque()

        # Cost tracking
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._max_usd = cfg.llm.cost_guard.max_usd_per_run
        self._warn_usd = cfg.llm.cost_guard.warn_usd_per_run
        self._warned = False

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
            if not self._api_key:
                raise ValueError(
                    f"Anthropic API key not set. "
                    f"Set the {self._cfg.llm.api_key_env} environment variable."
                )
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    # --- Rate limiting ---

    def _wait_for_rate_limit(self, estimated_tokens: int) -> None:
        now = time.monotonic()
        # Evict entries older than 60 seconds
        while self._window and self._window[0][0] < now - 60:
            self._window.popleft()

        requests_in_window = len(self._window)
        tokens_in_window = sum(t for _, t in self._window)

        rpm_wait = 0.0
        if requests_in_window >= self._rpm:
            rpm_wait = 60 - (now - self._window[0][0])

        tpm_wait = 0.0
        if tokens_in_window + estimated_tokens > self._tpm:
            tpm_wait = 60 - (now - self._window[0][0])

        wait = max(rpm_wait, tpm_wait, 0)
        if wait > 0:
            time.sleep(wait)
            # Re-evict after sleeping
            now = time.monotonic()
            while self._window and self._window[0][0] < now - 60:
                self._window.popleft()

        self._window.append((time.monotonic(), estimated_tokens))

    # --- Cost tracking ---

    def _check_cost(self, new_input: int, new_output: int) -> None:
        self._total_input_tokens += new_input
        self._total_output_tokens += new_output
        cost = self.total_cost_usd()

        if not self._warned and cost >= self._warn_usd:
            print(f"\n[wiki-builder] Cost warning: ${cost:.2f} spent so far (warn threshold: ${self._warn_usd:.2f})")
            self._warned = True

        if cost >= self._max_usd:
            raise CostGuardError(
                f"Cost guard triggered: ${cost:.2f} >= limit ${self._max_usd:.2f}. "
                "Increase llm.cost_guard.max_usd_per_run in wiki.yaml to continue."
            )

    def total_cost_usd(self) -> float:
        p = _price(self._model)
        return (
            self._total_input_tokens / 1_000_000 * p["input"]
            + self._total_output_tokens / 1_000_000 * p["output"]
        )

    def estimate_cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        p = _price(self._model)
        return (
            input_tokens / 1_000_000 * p["input"]
            + output_tokens / 1_000_000 * p["output"]
        )

    def print_cost_summary(self) -> None:
        cost = self.total_cost_usd()
        print(
            f"\n[LLM cost] {self._model} | "
            f"input: {self._total_input_tokens:,} tok, "
            f"output: {self._total_output_tokens:,} tok, "
            f"total: ${cost:.4f}"
        )

    # --- Completion ---

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> LLMResponse:
        if max_tokens is None:
            max_tokens = self._max_tokens

        cache_key = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
        if cache_key in self._prompt_cache:
            cached = self._prompt_cache[cache_key]
            return LLMResponse(text=cached.text, input_tokens=0, output_tokens=0, cached=True)

        # Rough token estimate: 1 token ≈ 4 chars
        estimated_input = (len(system) + len(user)) // 4
        self._wait_for_rate_limit(estimated_input)

        client = self._get_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = message.content[0].text
        resp = LLMResponse(
            text=text,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        self._prompt_cache[cache_key] = resp
        self._check_cost(resp.input_tokens, resp.output_tokens)
        return resp
