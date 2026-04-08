"""
wiki/article.py — Render individual wiki article .md files.

An article is produced for every source file. Its content comes from:
  - File metadata (path, type, size, date)
  - Extracted raw text (from extractor.py)
  - LLM summarization result (from operations/ingest.py)
  - Cross-references (added in a second pass)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import WikiConfig
from ..extractor import (
    format_size,
    get_fence_lang,
    get_file_tag,
    mtime_str,
    path_to_uri,
    slugify,
)
from ..state import LLMCacheEntry


def wikilink_name(wiki_root: Path, article_path: Path) -> str:
    """Return the Obsidian wikilink slug (path without extension, relative to vault root)."""
    try:
        rel = article_path.relative_to(wiki_root)
        return str(rel.with_suffix("")).replace("\\", "/")
    except ValueError:
        return article_path.stem


def make_breadcrumb(wiki_dir: Path, wiki_root: Path) -> str:
    """Build wikilink breadcrumb trail from wiki_root down to wiki_dir."""
    try:
        parts = wiki_dir.relative_to(wiki_root).parts
    except ValueError:
        return ""
    if not parts:
        return ""

    crumbs = []
    for i, part in enumerate(parts):
        ancestor_dir = wiki_root.joinpath(*parts[: i + 1])
        ancestor_index = ancestor_dir / f"{ancestor_dir.name}.md"
        wl = wikilink_name(wiki_root, ancestor_index)
        if i < len(parts) - 1:
            crumbs.append(f"[[{wl}|{part}]]")
        else:
            crumbs.append(f"**{part}**")

    return " > ".join(crumbs)


def article_wiki_path(source_file: Path, wiki_dir: Path) -> Path:
    """Return the wiki .md path for a given source file."""
    slug = slugify(source_file.stem)
    return wiki_dir / f"{slug}{source_file.suffix.lower()}.md"


def render_article(
    source_file: Path,
    wiki_file: Path,
    wiki_root: Path,
    file_type: str,
    raw_content: str,
    llm_result: LLMCacheEntry | None,
    cfg: WikiConfig,
) -> str:
    """
    Render a complete wiki article as a Markdown string.

    If llm_result is None (--no-llm mode), falls back to a rule-based summary
    extracted from the raw content.
    """
    today = date.today().isoformat()
    mod_date = mtime_str(source_file)
    fence_lang = get_fence_lang(source_file)
    file_tag = get_file_tag(source_file)

    # Tags: file type tag + LLM-suggested tags
    tags = [file_tag]
    if llm_result and llm_result.suggested_tags:
        for t in llm_result.suggested_tags:
            if t not in tags and t in cfg.tagging.tag_taxonomy:
                tags.append(t)

    try:
        size_str = format_size(source_file.stat().st_size)
    except Exception:
        size_str = "unknown"

    breadcrumb = make_breadcrumb(wiki_file.parent, wiki_root)
    source_uri = path_to_uri(source_file)

    # Summary section
    if llm_result and llm_result.summary:
        summary_text = llm_result.summary
    else:
        summary_text = _rule_based_summary(file_type, raw_content, source_file)

    # Key entities section
    entities_block = ""
    if llm_result and llm_result.key_entities:
        lines = [f"- {e}" for e in llm_result.key_entities]
        entities_block = "\n## Key Entities\n\n" + "\n".join(lines)

    # Raw content block (truncated)
    max_chars = cfg.llm.max_input_chars
    display_content = raw_content if len(raw_content) <= max_chars else (
        raw_content[:max_chars] + f"\n\n*[… truncated at {max_chars:,} chars — full file at source link]*"
    )

    # Related / wikilinks section
    related_block = ""
    if llm_result and llm_result.wikilinks:
        lines = [
            f"- [[{wl['target']}]] — {wl.get('reason', '')}"
            for wl in llm_result.wikilinks
        ]
        related_block = "\n## Related\n\n" + "\n".join(lines)

    # LLM model tag for frontmatter
    llm_model = cfg.llm.model if (llm_result and llm_result.summary) else ""

    tags_yaml = "[" + ", ".join(tags) + "]"

    crumb_line = f"\n{breadcrumb}\n" if breadcrumb else ""

    article = f"""---
source: {source_file}
file_type: {file_type}
file_size: {size_str}
last_modified: {mod_date}
wiki_updated: {today}
tags: {tags_yaml}
---
{crumb_line}
# {source_file.stem}

> [!info] {file_type} · {size_str} · Modified {mod_date}
> [Open source file]({source_uri})
## Summary

{summary_text}
{entities_block}
## Content

```{fence_lang}
{display_content}
```
{related_block}
"""
    return article


def write_article(
    source_file: Path,
    wiki_file: Path,
    wiki_root: Path,
    file_type: str,
    raw_content: str,
    llm_result: LLMCacheEntry | None,
    cfg: WikiConfig,
) -> None:
    """Render and write the wiki article to disk."""
    wiki_file.parent.mkdir(parents=True, exist_ok=True)
    text = render_article(
        source_file, wiki_file, wiki_root,
        file_type, raw_content, llm_result, cfg,
    )
    wiki_file.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Rule-based fallback summary (used when LLM is disabled)
# ---------------------------------------------------------------------------

def _rule_based_summary(file_type: str, content: str, source_file: Path) -> str:
    ext = source_file.suffix.lower()

    if ext in (".sql", ".lpd"):
        first = content.lstrip()
        if first.startswith("/*"):
            end = first.find("*/")
            if end != -1:
                comment = first[2:end].strip()
                if comment:
                    return comment[:300]
        comment_lines = []
        for line in first.splitlines():
            s = line.strip()
            if s.startswith("--"):
                comment_lines.append(s.lstrip("- ").strip())
            else:
                break
        if comment_lines:
            return " ".join(comment_lines)[:300]
        upper = first.upper()
        for kw in ("MERGE", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER",
                   "DROP", "SELECT", "WITH", "EXEC", "EXECUTE", "DECLARE"):
            if upper.startswith(kw):
                tokens = first.split()
                obj = tokens[1].split(".")[-1] if len(tokens) >= 2 else ""
                return f"{kw.capitalize()} operation{' on ' + obj if obj else ''}."
        return f"{file_type} file."

    if ext == ".py":
        stripped = content.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            q = stripped[:3]
            end = stripped.find(q, 3)
            if end != -1:
                doc = stripped[3:end].strip()
                if doc:
                    return doc[:300]
        comment_lines = []
        for line in stripped.splitlines():
            s = line.strip()
            if s.startswith("#"):
                comment_lines.append(s.lstrip("# ").strip())
            elif not s:
                continue
            else:
                break
        if comment_lines:
            return " ".join(comment_lines)[:300]
        return "Python script."

    if ext == ".ps1":
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("#"):
                return s.lstrip("# ").strip()
        return "PowerShell script."

    for line in content.splitlines():
        s = line.strip()
        if s and not s.startswith("*("):
            return s[:300]

    return f"{file_type} file."
