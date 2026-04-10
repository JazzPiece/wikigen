"""
config.py — WikiConfig dataclass and YAML loader.

Each wiki project has a wiki.yaml at its root. This module loads and validates
that file into a typed WikiConfig object consumed by all other modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

@dataclass
class ProjectConfig:
    name: str = "My Wiki"
    description: str = ""
    obsidian_vault: bool = True


@dataclass
class SourceConfig:
    path: str = "./docs"
    exclude_folders: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=lambda: [
        "~$*", "Thumbs.db", "desktop.ini", ".DS_Store",
    ])
    max_file_size_mb: float = 50.0
    text_extensions: list[str] = field(default_factory=lambda: [
        ".txt", ".sql", ".py", ".html", ".htm", ".csv",
        ".xml", ".json", ".md", ".bat", ".ps1", ".sh",
        ".lpd", ".log", ".ini", ".cfg", ".yaml", ".yml",
        ".js", ".ts", ".css", ".vbs", ".wsf", ".toml",
        ".vtt",
    ])
    rich_extensions: list[str] = field(default_factory=lambda: [
        ".docx", ".dotx", ".xlsx", ".xltx", ".pdf", ".pptx",
        ".eml", ".msg", ".vsdx",
    ])


@dataclass
class WikiConfig_Wiki:
    path: str = "./wiki"
    max_path_length: int = 240


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 50
    tokens_per_minute: int = 40_000


@dataclass
class CostGuardConfig:
    max_usd_per_run: float = 5.00
    warn_usd_per_run: float = 2.00


@dataclass
class LLMConfig:
    backend: str = "claude-api"          # "claude-api" | "openai-compat" | "claude-code"
    model: str = "claude-sonnet-4-6"
    api_key_env: str = "ANTHROPIC_API_KEY"
    api_key: str = ""                    # Literal key (for local servers that ignore auth)
    base_url: str = ""                   # For openai-compat (Ollama, OpenRouter, etc.)
    max_tokens_per_call: int = 2048
    max_input_chars: int = 15_000
    chunk_overlap_chars: int = 500
    cache: bool = True
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cost_guard: CostGuardConfig = field(default_factory=CostGuardConfig)

    def resolve_api_key(self) -> str:
        """Return the API key from literal value or environment variable."""
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env, "")


@dataclass
class SummarizationConfig:
    enabled: bool = True
    style: str = "concise"          # "concise" | "detailed" | "bullets"
    max_summary_words: int = 150
    include_key_entities: bool = True


@dataclass
class CrossRefConfig:
    enabled: bool = True
    min_confidence: float = 0.7
    max_links_per_article: int = 10


@dataclass
class TaggingConfig:
    auto_tags: bool = True
    tag_taxonomy: list[str] = field(default_factory=lambda: [
        "sql", "python", "word", "excel", "pdf", "powerpoint",
        "script", "data", "documentation", "process",
        "configuration", "report", "reference", "folder-index",
        "html", "xml", "json", "markdown", "other",
    ])


@dataclass
class ObsidianGroupsConfig:
    enabled: bool = True
    # Per-folder color overrides. Keys are folder names (case-sensitive),
    # values are #RRGGBB hex strings. Unspecified folders get palette colors.
    # Example:
    #   folder_colors:
    #     Backstitch: "#e84545"
    #     NEM: "#3d9be9"
    folder_colors: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

@dataclass
class WikiConfig:
    project: ProjectConfig = field(default_factory=ProjectConfig)
    source: SourceConfig = field(default_factory=SourceConfig)
    wiki: WikiConfig_Wiki = field(default_factory=WikiConfig_Wiki)
    llm: LLMConfig = field(default_factory=LLMConfig)
    summarization: SummarizationConfig = field(default_factory=SummarizationConfig)
    cross_references: CrossRefConfig = field(default_factory=CrossRefConfig)
    tagging: TaggingConfig = field(default_factory=TaggingConfig)
    obsidian_groups: ObsidianGroupsConfig = field(default_factory=ObsidianGroupsConfig)
    schema_file: str = "./CLAUDE.md"
    log_file: str = "./wiki/log.md"
    index_file: str = "./wiki/index.md"

    # Resolved absolute paths (set after loading, relative to config file dir)
    _config_dir: Path = field(default_factory=Path.cwd, repr=False)

    def source_path(self) -> Path:
        return (self._config_dir / self.source.path).resolve()

    def wiki_path(self) -> Path:
        return (self._config_dir / self.wiki.path).resolve()

    def schema_path(self) -> Path:
        return (self._config_dir / self.schema_file).resolve()

    def log_path(self) -> Path:
        p = Path(self.log_file)
        return p if p.is_absolute() else (self._config_dir / p).resolve()

    def index_path(self) -> Path:
        p = Path(self.index_file)
        return p if p.is_absolute() else (self._config_dir / p).resolve()

    def text_extensions_set(self) -> set[str]:
        return {e if e.startswith(".") else f".{e}" for e in self.source.text_extensions}

    def rich_extensions_set(self) -> set[str]:
        return {e if e.startswith(".") else f".{e}" for e in self.source.rich_extensions}

    def load_schema(self) -> str:
        """Load CLAUDE.md schema file as a string (used as LLM system prompt)."""
        p = self.schema_path()
        if p.exists():
            return p.read_text(encoding="utf-8")
        return _DEFAULT_SYSTEM_PROMPT.format(
            project_name=self.project.name,
            tag_taxonomy=", ".join(self.tagging.tag_taxonomy),
            max_summary_words=self.summarization.max_summary_words,
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> WikiConfig:
    """Load wiki.yaml from path and return a WikiConfig."""
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Run `wikigen init` to create one."
        )

    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    cfg = WikiConfig(_config_dir=config_path.parent.resolve())

    if "project" in raw:
        p = raw["project"]
        cfg.project = ProjectConfig(
            name=p.get("name", cfg.project.name),
            description=p.get("description", cfg.project.description),
            obsidian_vault=p.get("obsidian_vault", cfg.project.obsidian_vault),
        )

    if "source" in raw:
        s = raw["source"]
        cfg.source = SourceConfig(
            path=s.get("path", cfg.source.path),
            exclude_folders=s.get("exclude_folders", cfg.source.exclude_folders),
            exclude_patterns=s.get("exclude_patterns", cfg.source.exclude_patterns),
            max_file_size_mb=s.get("max_file_size_mb", cfg.source.max_file_size_mb),
            text_extensions=s.get("text_extensions", cfg.source.text_extensions),
            rich_extensions=s.get("rich_extensions", cfg.source.rich_extensions),
        )

    if "wiki" in raw:
        w = raw["wiki"]
        cfg.wiki = WikiConfig_Wiki(
            path=w.get("path", cfg.wiki.path),
            max_path_length=w.get("max_path_length", cfg.wiki.max_path_length),
        )

    if "llm" in raw:
        llm_raw = raw["llm"]
        rl_raw = llm_raw.get("rate_limit", {})
        cg_raw = llm_raw.get("cost_guard", {})
        cfg.llm = LLMConfig(
            backend=llm_raw.get("backend", cfg.llm.backend),
            model=llm_raw.get("model", cfg.llm.model),
            api_key_env=llm_raw.get("api_key_env", cfg.llm.api_key_env),
            api_key=llm_raw.get("api_key", cfg.llm.api_key),
            base_url=llm_raw.get("base_url", cfg.llm.base_url),
            max_tokens_per_call=llm_raw.get("max_tokens_per_call", cfg.llm.max_tokens_per_call),
            max_input_chars=llm_raw.get("max_input_chars", cfg.llm.max_input_chars),
            chunk_overlap_chars=llm_raw.get("chunk_overlap_chars", cfg.llm.chunk_overlap_chars),
            cache=llm_raw.get("cache", cfg.llm.cache),
            rate_limit=RateLimitConfig(
                requests_per_minute=rl_raw.get("requests_per_minute", 50),
                tokens_per_minute=rl_raw.get("tokens_per_minute", 40_000),
            ),
            cost_guard=CostGuardConfig(
                max_usd_per_run=cg_raw.get("max_usd_per_run", 5.00),
                warn_usd_per_run=cg_raw.get("warn_usd_per_run", 2.00),
            ),
        )

    if "summarization" in raw:
        s = raw["summarization"]
        cfg.summarization = SummarizationConfig(
            enabled=s.get("enabled", cfg.summarization.enabled),
            style=s.get("style", cfg.summarization.style),
            max_summary_words=s.get("max_summary_words", cfg.summarization.max_summary_words),
            include_key_entities=s.get("include_key_entities", cfg.summarization.include_key_entities),
        )

    if "cross_references" in raw:
        cr = raw["cross_references"]
        cfg.cross_references = CrossRefConfig(
            enabled=cr.get("enabled", cfg.cross_references.enabled),
            min_confidence=cr.get("min_confidence", cfg.cross_references.min_confidence),
            max_links_per_article=cr.get("max_links_per_article", cfg.cross_references.max_links_per_article),
        )

    if "tagging" in raw:
        t = raw["tagging"]
        cfg.tagging = TaggingConfig(
            auto_tags=t.get("auto_tags", cfg.tagging.auto_tags),
            tag_taxonomy=t.get("tag_taxonomy", cfg.tagging.tag_taxonomy),
        )

    if "obsidian_groups" in raw:
        og = raw["obsidian_groups"]
        cfg.obsidian_groups = ObsidianGroupsConfig(
            enabled=og.get("enabled", cfg.obsidian_groups.enabled),
            folder_colors=og.get("folder_colors", cfg.obsidian_groups.folder_colors) or {},
        )

    cfg.schema_file = raw.get("schema_file", cfg.schema_file)
    cfg.log_file = raw.get("log_file", cfg.log_file)
    cfg.index_file = raw.get("index_file", cfg.index_file)

    return cfg


# ---------------------------------------------------------------------------
# Default system prompt (fallback if CLAUDE.md not found)
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """\
You are a wiki maintainer for the "{project_name}" knowledge base.
Your output is always structured Markdown suitable for Obsidian.

The wiki uses these tag conventions: {tag_taxonomy}

Rules:
- Write concise, accurate content. Never hallucinate — only summarize what is in the source.
- Summaries must be under {max_summary_words} words.
- Use plain English. Avoid jargon unless it appears in the source.
- Return JSON exactly as requested — no extra text before or after the JSON block.
"""
