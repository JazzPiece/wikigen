"""
llm/openai_compat.py — OpenAI-compatible API backend.

Covers any server implementing the OpenAI chat completions API:
  - OpenAI (api.openai.com)
  - Ollama (localhost:11434/v1)  — local, free
  - LM Studio (localhost:1234/v1) — local, free
  - OpenRouter (openrouter.ai/api/v1) — multi-provider gateway
  - Groq (api.groq.com/openai/v1) — fast inference
  - Together AI, Mistral, Anyscale, etc.
  - Any vLLM / llama.cpp server

wiki.yaml examples:

  # Ollama (local)
  llm:
    backend: openai-compat
    base_url: "http://localhost:11434/v1"
    model: "llama3.2"
    api_key: "ollama"

  # OpenRouter
  llm:
    backend: openai-compat
    base_url: "https://openrouter.ai/api/v1"
    model: "google/gemini-2.0-flash-001"
    api_key_env: OPENROUTER_API_KEY
"""

from __future__ import annotations

import hashlib

from ..config import WikiConfig
from .base import LLMBackend, LLMResponse


class OpenAICompatBackend(LLMBackend):
    def __init__(self, cfg: WikiConfig) -> None:
        self._cfg = cfg
        self._model = cfg.llm.model
        self._base_url = cfg.llm.base_url or "https://api.openai.com/v1"
        self._api_key = cfg.llm.resolve_api_key() or "local"
        self._max_tokens = cfg.llm.max_tokens_per_call
        self._client = None  # Lazy-initialised

        # In-memory response cache
        self._prompt_cache: dict[str, LLMResponse] = {}

        # Token counters (best-effort — some local servers don't return usage)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install openai"
                )
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
            )
        return self._client

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> LLMResponse:
        if max_tokens is None:
            max_tokens = self._max_tokens

        cache_key = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
        if cache_key in self._prompt_cache:
            cached = self._prompt_cache[cache_key]
            return LLMResponse(text=cached.text, cached=True)

        client = self._get_client()
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = response.choices[0].message.content or ""

        # Usage may be None for some local servers
        input_tokens = 0
        output_tokens = 0
        if response.usage:
            input_tokens = response.usage.prompt_tokens or 0
            output_tokens = response.usage.completion_tokens or 0

        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens

        resp = LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self._prompt_cache[cache_key] = resp
        return resp

    def estimate_cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        # Local servers are free; remote pricing varies. Return 0 as default.
        # Users can override this by subclassing if needed.
        return 0.0

    def print_cost_summary(self) -> None:
        print(
            f"\n[LLM usage] {self._model} @ {self._base_url} | "
            f"input: {self._total_input_tokens:,} tok, "
            f"output: {self._total_output_tokens:,} tok"
        )
