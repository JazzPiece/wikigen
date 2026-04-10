"""
wiki/crossref.py — Two-pass cross-reference generation.

After all articles are summarized, a second LLM pass computes wikilinks
between articles. The LLM sees a compact index of all pages and returns
high-confidence links for each article.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import WikiConfig
from ..llm.base import LLMBackend
from ..state import LLMCacheEntry, WikiState


def build_crossref_prompt(
    article_title: str,
    article_summary: str,
    article_entities: list[str],
    wiki_index: list[dict],
    cfg: WikiConfig,
) -> str:
    index_lines = "\n".join(
        f"- [[{p['slug']}]] — {p.get('summary', '')[:100]}"
        for p in wiki_index[:300]
    )
    return f"""Current article: "{article_title}"
Summary: {article_summary}
Key entities: {', '.join(article_entities)}

Existing wiki pages (slug and brief summary):
{index_lines}

Task: Identify which existing wiki pages are meaningfully related to "{article_title}".

Return ONLY a JSON object, no other text:
{{
  "wikilinks": [
    {{"target": "page-slug", "reason": "brief reason why it is related", "confidence": 0.0}}
  ]
}}

Rules:
- Only include links with confidence >= {cfg.cross_references.min_confidence}
- Maximum {cfg.cross_references.max_links_per_article} links
- Do not link to the article itself
- If no strong links exist, return {{"wikilinks": []}}
"""


def _parse_wikilinks(text: str) -> list[dict]:
    """Extract wikilinks JSON from LLM response, tolerating minor formatting."""
    try:
        data = json.loads(text)
        return data.get("wikilinks", [])
    except json.JSONDecodeError:
        pass
    # Try to find JSON block inside markdown fence
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return data.get("wikilinks", [])
        except json.JSONDecodeError:
            pass
    return []


def compute_cross_references(
    changed_articles: list[dict],   # [{slug, title, summary, entities, source_file}]
    all_articles: list[dict],        # Full wiki index for context
    llm: LLMBackend,
    state: WikiState,
    cfg: WikiConfig,
    system_prompt: str,
    dry_run: bool = False,
) -> dict[str, list[dict]]:
    """
    For each changed article, ask the LLM to generate wikilinks to related pages.

    Returns: {slug: [wikilink_dict, ...]}
    """
    results: dict[str, list[dict]] = {}

    if not cfg.cross_references.enabled:
        return results

    # Build index without the current article (to avoid self-links)
    wiki_index = [
        {"slug": a["slug"], "summary": a.get("summary", "")}
        for a in all_articles
    ]

    for article in changed_articles:
        slug = article["slug"]
        title = article.get("title", slug)
        summary = article.get("summary", "")
        entities = article.get("entities", [])
        source_file = article.get("source_file", "")

        # Build index without self
        index_for_this = [p for p in wiki_index if p["slug"] != slug]

        user_prompt = build_crossref_prompt(title, summary, entities, index_for_this, cfg)

        if dry_run:
            results[slug] = []
            continue

        try:
            resp = llm.complete(system_prompt, user_prompt, max_tokens=1024)
            wikilinks = _parse_wikilinks(resp.text)

            # Filter by confidence threshold
            wikilinks = [
                wl for wl in wikilinks
                if wl.get("confidence", 0) >= cfg.cross_references.min_confidence
            ]
            wikilinks = wikilinks[: cfg.cross_references.max_links_per_article]

            results[slug] = wikilinks

            # Persist into LLM cache for the article
            if source_file:
                file_state = state.get_file_state(Path(source_file))
                if file_state and file_state.llm_cache_key:
                    cached = state.get_llm_cache(file_state.llm_cache_key)
                    if cached:
                        cached.wikilinks = wikilinks
                        state.set_llm_cache(file_state.llm_cache_key, cached)

        except Exception as e:
            print(f"  [crossref error] {title}: {e}")
            results[slug] = []

    return results
