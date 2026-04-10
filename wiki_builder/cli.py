"""
cli.py — Click-based CLI entry point for wikigen.

Commands:
  init    — Scaffold a new wiki project (wiki.yaml + CLAUDE.md)
  ingest  — Process source files and update the wiki
  query   — Ask a question against the wiki
  lint    — Health-check the wiki
  status  — Show state summary without processing
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console(highlight=False)


class _TeeStream:
    """Write to both the original stream and a log file simultaneously."""

    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file

    def write(self, data):
        self._stream.write(data)
        self._log.write(data)

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextmanager
def _tee_output(log_path: Path):
    """Context manager: tee stdout and stderr to log_path for the duration."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as lf:
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout = _TeeStream(orig_out, lf)
        sys.stderr = _TeeStream(orig_err, lf)
        try:
            yield
        finally:
            sys.stdout = orig_out
            sys.stderr = orig_err


def _safe_rule(title: str = "") -> None:
    """Print a separator line, falling back to plain text on encoding errors."""
    try:
        console.rule(title)
    except Exception:
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
@click.version_option(package_name="wikigen")
def cli() -> None:
    """wikigen — Universal LLM-powered wiki builder for any folder."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--source", default=".", show_default=True, type=click.Path(),
              help="Source folder to index (default: current directory).")
@click.option("--wiki", default=None,
              help="Wiki output folder (default: <source>/wiki).")
@click.option("--name", default="", help="Project name. Defaults to source folder name.")
@click.option("--backend", default="claude-code",
              type=click.Choice(["claude-api", "openai-compat", "claude-code"], case_sensitive=False),
              show_default=True, help="LLM backend.")
@click.option("--model", default="", help="Model name. Defaults to backend default.")
@click.option("--api-key-env", default="ANTHROPIC_API_KEY", show_default=True,
              help="Env var holding the API key (claude-api / openai-compat only).")
@click.option("--base-url", default="", help="Base URL for openai-compat backend.")
@click.option("--force", is_flag=True, help="Overwrite existing wiki.yaml without prompting.")
def init(
    source: str, wiki: str | None, name: str,
    backend: str, model: str, api_key_env: str, base_url: str,
    force: bool,
) -> None:
    """Scaffold a new wiki project (wiki.yaml + CLAUDE.md).

    All options have safe defaults so the simplest usage is just:

        wikigen init

    Run from the folder that contains the files you want to index.
    The wiki is created inside the source folder by default.
    """
    cwd = Path.cwd()
    src_path = (cwd / source).resolve()

    if not name:
        name = src_path.name

    # Default wiki to <source>/wiki
    if not wiki:
        wiki = str(src_path / "wiki")

    if not model:
        model = {
            "claude-api": "claude-sonnet-4-6",
            "claude-code": "claude-sonnet-4-6",
            "openai-compat": "llama3.2",
        }.get(backend, "claude-sonnet-4-6")

    wiki_yaml = cwd / "wiki.yaml"
    if wiki_yaml.exists() and not force:
        click.echo(f"wiki.yaml already exists. Use --force to overwrite.")
        return

    base_url_line = f'  base_url: "{base_url}"' if base_url else ""
    api_key_line = (
        f'  api_key_env: "{api_key_env}"'
        if backend in ("claude-api", "openai-compat") and api_key_env
        else '  api_key: "local"'
    )

    # Auto-detect extra exclude folders present in source
    auto_excludes = []
    for hidden in (".vscode", "node_modules", "__pycache__"):
        if (src_path / hidden).exists():
            auto_excludes.append(hidden)

    extra_excludes = ""
    for folder in auto_excludes:
        extra_excludes += f'    - "{folder}"\n'

    wiki_dirname = Path(wiki).name  # e.g. "./wiki" -> "wiki"
    # Normalize to forward slashes so Windows paths don't break YAML double-quoted strings
    source_yaml = str(source).replace("\\", "/")
    wiki_yaml_path = str(wiki).replace("\\", "/")
    wiki_yaml.write_text(
        _WIKI_YAML_TEMPLATE.format(
            name=name,
            source=source_yaml,
            wiki=wiki_yaml_path,
            wiki_dirname=wiki_dirname,
            backend=backend,
            model=model,
            base_url_line=base_url_line,
            api_key_line=api_key_line,
            extra_excludes=extra_excludes,
        ),
        encoding="utf-8",
    )
    click.echo(f"  Created: {wiki_yaml}")

    claude_md = cwd / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(_CLAUDE_MD_TEMPLATE.format(name=name), encoding="utf-8")
        click.echo(f"  Created: {claude_md}")
    else:
        click.echo(f"  Skipped: {claude_md} (already exists — edit it to customize the LLM prompts)")

    wiki_dir = cwd / wiki
    wiki_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"  Created: {wiki_dir}/")

    if auto_excludes:
        click.echo(f"  Auto-excluded: {', '.join(auto_excludes)}")

    click.echo("\nNext steps:")
    click.echo("  wikigen ingest --no-llm     # fast extraction, no API calls")
    click.echo("  wikigen ingest              # full run with LLM distillation")
    click.echo("\nSecurity: never commit wiki.yaml if it contains a real API key.")


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
@click.option("--log-file", default=None, type=click.Path(),
              help="Write full run transcript to this file (stdout + stderr).")
def ingest(
    config: str,
    incremental: bool,
    no_llm: bool,
    no_crossref: bool,
    dry_run: bool,
    verbose: bool,
    llm_backend: str | None,
    log_file: str | None,
) -> None:
    """Process source files and write wiki articles."""
    if log_file:
        with _tee_output(Path(log_file)):
            _run_ingest(config, incremental, no_llm, no_crossref, dry_run, verbose, llm_backend)
    else:
        _run_ingest(config, incremental, no_llm, no_crossref, dry_run, verbose, llm_backend)


def _run_ingest(
    config: str,
    incremental: bool,
    no_llm: bool,
    no_crossref: bool,
    dry_run: bool,
    verbose: bool,
    llm_backend: str | None,
) -> None:
    from .config import load_config
    from .operations.ingest import run_ingest
    from .state import WikiState

    cfg = load_config(Path(config))

    llm = None
    if not no_llm:
        llm = _create_llm(cfg, llm_backend)

    state = WikiState(cfg.wiki_path())
    state.load()

    _safe_rule(f"wikigen ingest - {cfg.project.name}")
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

    _safe_rule("wikigen query")
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

    _safe_rule("wikigen lint")
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
  exclude_folders:
    - "{wiki_dirname}"      # never re-index wiki output
    - ".obsidian"           # Obsidian vault metadata
    - ".git"
{extra_excludes}  exclude_patterns:
    - "~$*"
    - "Thumbs.db"
    - "desktop.ini"
    - ".DS_Store"
    - "wiki.yaml"
    - "CLAUDE.md"
  max_file_size_mb: 50

wiki:
  path: "{wiki}"
  max_path_length: 240

llm:
  backend: "{backend}"
  model: "{model}"
{base_url_line}
{api_key_line}
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

obsidian_groups:
  enabled: true
  # Optional: override auto-assigned colors per top-level folder.
  # Keys are folder names; values are #RRGGBB hex strings.
  # Unspecified folders get colors from a built-in palette.
  # folder_colors:
  #   MyFolder: "#e84545"
  #   AnotherFolder: "#3d9be9"

schema_file: "./CLAUDE.md"
log_file: "{wiki}/log.md"
index_file: "{wiki}/index.md"
"""

_CLAUDE_MD_TEMPLATE = """\
# Wiki Schema — {name}

This file is the LLM system prompt used on every wikigen operation.
Edit the sections below to tune behavior for your domain.

---

## Project Context

[Describe what this project is about. What kinds of documents does it contain?
Who are the key stakeholders? What decisions or processes does it document?
The LLM uses this to understand domain vocabulary and emphasis.]

## Distillation Style

The goal is a compact, high-signal knowledge base — not a summary archive.
For each document:
- Strip: cover pages, table of contents, legal boilerplate, repetitive headers, blank sections
- Keep: decisions, requirements, processes, findings, data, names, dates, technical specifics
- Restructure: if the source is poorly organized, rewrite into logical sections
- If the source is already concise and well-structured, preserve it mostly as-is

Notes should be dense — every sentence should carry information.

## Tag Taxonomy

Only use tags from this list in YAML frontmatter:
sql, python, word, excel, pdf, powerpoint, script, data, documentation,
process, configuration, report, reference, folder-index, html, xml, json,
markdown, other

## Wikilink Conventions

- Use `[[page-slug|Display Name]]` format
- Add a `## Related` section at the bottom of each article with wikilinks

## Article Section Order

1. YAML frontmatter (auto-generated)
2. Breadcrumb navigation (auto-generated)
3. H1 title
4. File metadata callout
5. `## Notes` — distilled content (LLM) or `## Summary` (extraction-only)
6. `## Key Entities` — people, systems, dates, frameworks
7. `## Content` — raw extracted text
8. `## Related` — wikilinks

## Output Rules

- Return JSON exactly as requested — no extra text before or after the JSON block.
- Never hallucinate. Only use information present in the source.
- When uncertain, omit rather than guess.
"""
