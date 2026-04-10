"""
operations/lint.py — Wiki health checks.

Detects: orphan pages, broken wikilinks, stale articles (source changed),
and missing entity pages (entity mentioned 3+ times without its own page).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import WikiConfig
from ..state import WikiState
from ..wiki.index import append_log


@dataclass
class LintReport:
    orphan_pages: list[str] = field(default_factory=list)
    broken_links: list[tuple[str, str]] = field(default_factory=list)  # (page, target)
    stale_articles: list[str] = field(default_factory=list)
    missing_entity_pages: list[str] = field(default_factory=list)

    def has_issues(self) -> bool:
        return any([
            self.orphan_pages,
            self.broken_links,
            self.stale_articles,
            self.missing_entity_pages,
        ])

    def print_report(self) -> None:
        if not self.has_issues():
            print("Wiki is healthy — no issues found.")
            return

        if self.orphan_pages:
            print(f"\nOrphan pages ({len(self.orphan_pages)}) — no inbound links:")
            for p in self.orphan_pages:
                print(f"  - {p}")

        if self.broken_links:
            print(f"\nBroken wikilinks ({len(self.broken_links)}):")
            for page, target in self.broken_links:
                print(f"  - [[{target}]] in {page}")

        if self.stale_articles:
            print(f"\nStale articles ({len(self.stale_articles)}) — source file changed:")
            for p in self.stale_articles:
                print(f"  - {p}")

        if self.missing_entity_pages:
            print(f"\nMissing entity pages ({len(self.missing_entity_pages)}) — mentioned 3+ times:")
            for e in self.missing_entity_pages:
                print(f"  - {e}")


def run_lint(
    cfg: WikiConfig,
    state: WikiState,
    fix: bool = False,
) -> LintReport:
    """Run all health checks and return a LintReport."""
    wiki_root = cfg.wiki_path()
    report = LintReport()

    if not wiki_root.exists():
        print("Wiki directory does not exist. Run `wikigen ingest` first.")
        return report

    # Collect all wiki .md files
    all_wiki_files = list(wiki_root.rglob("*.md"))
    all_slugs: set[str] = set()
    for wf in all_wiki_files:
        try:
            rel = wf.relative_to(wiki_root)
            slug = str(rel.with_suffix("")).replace("\\", "/")
            all_slugs.add(slug)
            # Also add stem-only for short references
            all_slugs.add(wf.stem)
        except ValueError:
            pass

    # --- Collect all wikilinks across the wiki ---
    inbound: dict[str, list[str]] = {slug: [] for slug in all_slugs}
    all_links: list[tuple[str, str]] = []  # (source_slug, target)

    for wf in all_wiki_files:
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            src_rel = wf.relative_to(wiki_root)
            src_slug = str(src_rel.with_suffix("")).replace("\\", "/")
        except ValueError:
            src_slug = wf.stem

        for match in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text):
            target = match.group(1).strip()
            all_links.append((src_slug, target))
            if target in inbound:
                inbound[target].append(src_slug)

    # --- Orphan pages (no inbound links, excluding index and log files) ---
    skip_names = {"index", "log", "_MASTER_INDEX"}
    for slug, sources in inbound.items():
        name = slug.split("/")[-1]
        if name in skip_names or name.endswith("index") or name.endswith("Index"):
            continue
        if not sources:
            report.orphan_pages.append(slug)

    # --- Broken wikilinks ---
    for src, target in all_links:
        # Normalize target
        norm = target.replace("\\", "/")
        if norm not in all_slugs and norm.split("/")[-1] not in all_slugs:
            report.broken_links.append((src, target))

    # --- Stale articles (source file changed since last summarization) ---
    for key in state.all_source_keys():
        sp = Path(key)
        if not sp.exists():
            continue
        fs = state.get_file_state(sp)
        if not fs:
            continue
        try:
            current_mtime = sp.stat().st_mtime
            if fs.mtime and abs(current_mtime - fs.mtime) > 1:
                # Check hash to confirm actual change
                from ..extractor import file_hash
                if file_hash(sp) != fs.hash:
                    report.stale_articles.append(str(sp))
        except Exception:
            pass

    # --- Missing entity pages ---
    entity_mentions: dict[str, int] = {}
    for wf in all_wiki_files:
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Look for Key Entities section
        in_entities = False
        for line in text.splitlines():
            if "## Key Entities" in line:
                in_entities = True
                continue
            if in_entities:
                if line.startswith("##"):
                    break
                m = re.match(r"^- \*\*[^*]+\*\*:\s*(.+)", line)
                if m:
                    for entity in m.group(1).split(","):
                        e = entity.strip()
                        if e:
                            entity_mentions[e] = entity_mentions.get(e, 0) + 1

    for entity, count in entity_mentions.items():
        if count >= 3:
            slug_guess = entity.lower().replace(" ", "-")
            if slug_guess not in all_slugs and entity not in all_slugs:
                report.missing_entity_pages.append(f"{entity} (mentioned {count}x)")

    if not fix:
        report.print_report()
    else:
        _apply_fixes(report, wiki_root, cfg)

    append_log(wiki_root, cfg, f"lint | {len(report.orphan_pages)} orphans, {len(report.broken_links)} broken links")
    return report


def _apply_fixes(report: LintReport, wiki_root: Path, cfg: WikiConfig) -> None:
    """Apply automatic fixes where possible."""
    # Currently: remove orphan pages that are clearly stale (source deleted)
    # Conservative — only remove if the source file is also gone
    print("Auto-fix: removing articles for deleted source files...")
    removed = 0
    for stale in report.stale_articles:
        sp = Path(stale)
        if not sp.exists():
            # Source is gone, wiki article is orphaned
            # (finding the wiki path would require state; just report)
            print(f"  Source deleted: {stale}")
            removed += 1
    if removed == 0:
        print("  Nothing to fix automatically.")
    else:
        print(f"  Flagged {removed} deleted source(s). Re-run `ingest` to clean wiki articles.")
