"""
cli.py — Click-based CLI entry point for wiki-builder.

Commands:
  init    — Scaffold a new wiki project (wiki.yaml + CLAUDE.md)
  ingest  — Process source files and update the wiki
  query   — Ask a question against the wiki
  lint    — Health-check the wiki
  status  — Show state summary without processing
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console(highlight=False)


def _safe_rule(title: str = "") -> None:
    """Print a separator line, falling back to plain text on encoding errors."""
    try:
        console.rule(title)
    except (UnicodeEncodeError, Exception):
        width = 60
        if title:
            print(f"--- {title} ---")
        else:
            print("-" * width)


# ---------------------------------------------------------------------------
# Shared option helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str) -> "WikiConfig":
    from .config import load_config
    return load_config(Path(config_path))


def _create_llm(cfg: "WikiConfig", backend_override: str | None = None) -> "LLMBackend":
    from .llm.factory import create_backend
    if backend_override and backend_override != "none":
        cfg.llm.backend = backend_override
    return create_backend(cfg)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="llm-wiki-builder")
def cli() -> None:
    """wiki-builder — Universal LLM-powered wiki builder for any folder."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--source", required=True, type=click.Path(), help="Source folder to index.")
@click.option("--wiki", default="./wiki", show_default=True, help="Wiki output folder.")
@click.option("--name", default="", help="Project name (used in headings and prompts).")
def init(source: str, wiki: str, name: str) -> None:
    """Scaffold a new wiki project (wiki.yaml + CLAUDE.md + .gitignore)."""
    cwd = Path.cwd()

    if not name:
        name = Path(source).resolve().name

    # Determine backend
    backend = click.prompt(
        "LLM backend",
        type=click.Choice(["claude-api", "openai-compat", "claude-code"], case_sensitive=False),
        default="claude-api",
    )

    model_default = "claude-sonnet-4-6" if backend == "claude-api" else "llama3.2"
    model = click.prompt("Model name", default=model_default)

    base_url = ""
    api_key_env = "ANTHROPIC_API_KEY"
    if backend == "openai-compat":
        base_url = click.prompt(
            "Base URL",
            default="http://localhost:11434/v1",
        )
        api_key_env = click.prompt("API key env var (or leave blank for local)", default="")

    # Write wiki.yaml
    wiki_yaml = cwd / "wiki.yaml"
    if wiki_yaml.exists() and not click.confirm(f"{wiki_yaml} already exists. Overwrite?"):
        click.echo("Aborted.")
        return

    base_url_line = f'  base_url: "{base_url}"' if base_url else ""
    api_key_env_line = f'  api_key_env: "{api_key_env}"' if api_key_env else '  api_key: "local"'

    wiki_yaml.write_text(
        _WIKI_YAML_TEMPLATE.format(
            name=name,
            source=source,
            wiki=wiki,
            backend=backend,
            model=model,
            base_url_line=base_url_line,
            api_key_env_line=api_key_env_line,
        ),
        encoding="utf-8",
    )
    click.echo(f"  Created: {wiki_yaml}")

    # Write CLAUDE.md
    claude_md = cwd / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(_CLAUDE_MD_TEMPLATE.format(name=name), encoding="utf-8")
        click.echo(f"  Created: {claude_md}")
    else:
        click.echo(f"  Skipped: {claude_md} (already exists)")

    # Create wiki directory
    wiki_dir = cwd / wiki
    wiki_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"  Created: {wiki_dir}/")

    click.echo(
        f"\nDone! Run `wiki-builder ingest --no-llm` for a quick test, "
        f"or `wiki-builder ingest` for full LLM summarization."
    )
    click.echo(
        "\nSecurity reminder:\n"
        "  - Never commit wiki.yaml if it contains a real API key (use api_key_env instead)\n"
        "  - Add wiki.yaml and .env to your .gitignore if they contain secrets"
    )


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", default="wiki.yaml", show_default=True, help="Path to wiki.yaml.")
@click.option("--incremental/--full", default=True, show_default=True,
              help="Skip unchanged files (incremental) or rebuild everything (full).")
@click.option("--no-llm", is_flag=True, help="Extract text only — no LLM calls.")
@click.option("--no-crossref", is_flag=True, help="Skip cross-reference pass.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without writing files.")
@click.option("--verbose", "-v", is_flag=True, help="Print every file processed.")
@click.option("--llm-backend", default=None, type=str,
              help="Override LLM backend from config (claude-api, openai-compat, claude-code).")
def ingest(
    config: str,
    incremental: bool,
    no_llm: bool,
    no_crossref: bool,
    dry_run: bool,
    verbose: bool,
    llm_backend: str | None,
) -> None:
    """Process source files and write wiki articles."""
    from .config import load_config
    from .llm.base import CostGuardError
    from .operations.ingest import run_ingest
    from .state import WikiState

    cfg = load_config(Path(config))

    llm = None
    if not no_llm:
        llm = _create_llm(cfg, llm_backend)

    state = WikiState(cfg.wiki_path())
    state.load()

    _safe_rule(f"wiki-builder ingest - {cfg.project.name}")
    click.echo(f"  Source  : {cfg.source_path()}")
    click.echo(f"  Wiki    : {cfg.wiki_path()}")
    click.echo(f"  Mode    : {'incremental' if incremental else 'full rebuild'}")
    if no_llm:
        click.echo("  LLM     : disabled (--no-llm)")
    else:
        click.echo(f"  LLM     : {cfg.llm.backend} / {cfg.llm.model}")
    click.echo()

    if dry_run:
        click.echo("  [DRY RUN - no files will be written]\n")

    result = run_ingest(
        cfg=cfg,
        llm=llm,
        state=state,
        incremental=incremental,
        dry_run=dry_run,
        verbose=verbose,
        no_crossref=no_crossref,
    )

    _safe_rule()
    click.echo(f"  Files scanned   : {result.total_files}")
    click.echo(f"  Articles written: {result.articles_written}")
    click.echo(f"  Articles skipped: {result.articles_skipped}")
    click.echo(f"  LLM summarized  : {result.articles_summarized}")
    click.echo(f"  Errors          : {result.errors}")

    if hasattr(llm, "print_cost_summary"):
        llm.print_cost_summary()

    if result.cost_aborted:
        click.echo("\nRun aborted by cost guard. Partial state saved.", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("question")
@click.option("--config", default="wiki.yaml", show_default=True)
@click.option("--save", is_flag=True, help="Save answer as a new wiki page.")
@click.option("--llm-backend", default=None, type=str)
def query(question: str, config: str, save: bool, llm_backend: str | None) -> None:
    """Ask a question against the wiki and get an LLM-synthesized answer."""
    from .operations.query import run_query

    cfg = _load_config(config)
    llm = _create_llm(cfg, llm_backend)

    _safe_rule("wiki-builder query")
    click.echo(f"  Q: {question}\n")

    answer = run_query(
        question=question,
        cfg=cfg,
        llm=llm,
        save_to_wiki=save,
    )
    click.echo(answer)


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", default="wiki.yaml", show_default=True)
@click.option("--fix", is_flag=True, help="Apply automatic fixes where possible.")
def lint(config: str, fix: bool) -> None:
    """Health-check the wiki for orphans, broken links, and stale articles."""
    from .operations.lint import run_lint
    from .state import WikiState

    cfg = _load_config(config)
    state = WikiState(cfg.wiki_path())
    state.load()

    _safe_rule("wiki-builder lint")
    run_lint(cfg=cfg, state=state, fix=fix)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", default="wiki.yaml", show_default=True)
def status(config: str) -> None:
    """Show a summary of the current wiki state without processing anything."""
    from .state import WikiState

    cfg = _load_config(config)
    state = WikiState(cfg.wiki_path())
    state.load()

    wiki_root = cfg.wiki_path()
    total_articles = sum(1 for _ in wiki_root.rglob("*.md")) if wiki_root.exists() else 0
    total_tracked = len(state.all_source_keys())

    table = Table(title=f"Wiki Status — {cfg.project.name}", show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value")
    table.add_row("Source folder", str(cfg.source_path()))
    table.add_row("Wiki folder", str(wiki_root))
    table.add_row("LLM backend", f"{cfg.llm.backend} / {cfg.llm.model}")
    table.add_row("Tracked source files", str(total_tracked))
    table.add_row("Wiki articles (.md)", str(total_articles))
    table.add_row("CLAUDE.md exists", "Yes" if cfg.schema_path().exists() else "No")

    console.print(table)


# ---------------------------------------------------------------------------
# Templates for init
# ---------------------------------------------------------------------------

_WIKI_YAML_TEMPLATE = """\
project:
  name: "{name}"
  obsidian_vault: true

source:
  path: "{source}"
  exclude_folders: []
  exclude_patterns:
    - "~$*"
    - "Thumbs.db"
    - "desktop.ini"
    - ".DS_Store"
  max_file_size_mb: 50

wiki:
  path: "{wiki}"
  max_path_length: 240

llm:
  backend: "{backend}"
  model: "{model}"
{base_url_line}
{api_key_env_line}
  max_input_chars: 15000
  chunk_overlap_chars: 500
  cache: true
  cost_guard:
    max_usd_per_run: 5.00
    warn_usd_per_run: 2.00

summarization:
  enabled: true
  max_summary_words: 150
  include_key_entities: true

cross_references:
  enabled: true
  min_confidence: 0.7
  max_links_per_article: 10

tagging:
  auto_tags: true
  tag_taxonomy:
    - sql
    - python
    - word
    - excel
    - pdf
    - powerpoint
    - script
    - data
    - documentation
    - process
    - configuration
    - report
    - reference
    - folder-index
    - html
    - xml
    - json
    - markdown
    - other

schema_file: "./CLAUDE.md"
log_file: "./{wiki}/log.md"
index_file: "./{wiki}/index.md"
"""

_CLAUDE_MD_TEMPLATE = """\
# Wiki Schema — {name}

This file defines the structure, conventions, and workflows for this wiki.
It is used as the LLM system prompt on every wiki-builder operation.
Edit it to tune the LLM's behavior for your domain.

---

## Project Context

[Describe what this wiki is about. The LLM uses this to understand domain
vocabulary and what kinds of documents are likely to appear.]

## Wiki Structure

- **Source summaries** — one page per source file: `wiki/<folder>/<file>.md`
- **index.md** — master content catalog, updated on every ingest
- **log.md** — append-only chronological record of operations
- **queries/** — Q&A results filed back into the wiki

## Tag Taxonomy

Only use tags from this list in YAML frontmatter:
sql, python, word, excel, pdf, powerpoint, script, data, documentation,
process, configuration, report, reference, folder-index, html, xml, json,
markdown, other

## Summary Style

- Summaries must be concise — approximately 150 words
- Always state: what the document is, what it contains, who it is for (if known)
- Use plain English. Avoid jargon unless it appears in the source.
- Do not invent information not present in the source.

## Wikilink Conventions

- Use `[[page-slug|Display Name]]` format
- Only link to pages that exist (or are likely to exist) in the wiki
- Add a "Related" section at the bottom of each article with wikilinks

## Article Section Order

1. YAML frontmatter
2. Breadcrumb navigation
3. H1 title (filename without extension)
4. File metadata callout (type, size, modified date, source link)
5. `## Summary` — LLM-generated
6. `## Key Entities` — bullet list of people, systems, concepts, dates
7. `## Content` — raw extracted text in code fence
8. `## Related` — wikilinks to related pages

## Output Rules

- Return JSON exactly as requested — no extra text before or after the JSON block.
- Never hallucinate. Only summarize what is present in the source content.
- When you are uncertain, say so.
"""
