"""
wiki/obsidian.py — Write Obsidian graph color groups to .obsidian/graph.json.

Called at the end of ingest when obsidian_groups.enabled is true.
Auto-assigns colors from a palette; user can override per-folder in wiki.yaml.

Merging strategy:
  - Read existing graph.json if present
  - Remove groups whose query matches any folder we manage (tag:#<folder>)
  - Prepend our groups (so they appear first in Obsidian's UI)
  - Preserve any other user-defined groups
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import WikiConfig


# ---------------------------------------------------------------------------
# Color palette (RGB tuples, 0-255 each)
# ---------------------------------------------------------------------------

_PALETTE: list[tuple[int, int, int]] = [
    (217,  69,  69),  # red
    ( 69, 140, 222),  # blue
    ( 69, 191, 114),  # green
    (237, 165,  51),  # amber
    (178,  89, 216),  # purple
    (242, 114,  51),  # orange
    ( 64, 204, 204),  # teal
    (229,  76, 165),  # pink
    (140, 204,  64),  # lime
    (102, 165, 229),  # sky blue
]


def _rgb_to_int(r: int, g: int, b: int) -> int:
    """Pack RGB channels into a single integer (Obsidian graph.json format)."""
    return (r << 16) | (g << 8) | b


def _hex_to_int(hex_color: str) -> int:
    """Convert a #RRGGBB hex string to Obsidian's packed integer format."""
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return _rgb_to_int(r, g, b)


def _build_group(tag: str, color_int: int) -> dict:
    return {
        "query": f"tag:#{tag}",
        "color": {"a": 1, "rgb": color_int},
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def write_obsidian_graph(wiki_root: Path, folders: list[str], cfg: WikiConfig) -> None:
    """
    Write / update .obsidian/graph.json with one color group per discovered folder.

    Parameters
    ----------
    wiki_root : Path
        Root of the wiki vault (where .obsidian/ lives).
    folders : list[str]
        Top-level folder names discovered during this ingest run (already-known
        folders from prior runs are merged from any existing graph.json).
    cfg : WikiConfig
        Config — used for obsidian_groups settings.
    """
    obsidian_dir = wiki_root / ".obsidian"
    obsidian_dir.mkdir(exist_ok=True)
    graph_path = obsidian_dir / "graph.json"

    # Load existing graph.json
    existing: dict = {}
    if graph_path.exists():
        try:
            existing = json.loads(graph_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Build the set of tags we manage (one per top-level folder)
    # Folder tag format matches article.py: folder_name.lower().replace(" ", "-")
    managed_tags: dict[str, int] = {}  # tag -> color_int

    user_colors: dict[str, str] = cfg.obsidian_groups.folder_colors

    # Assign colors to all discovered folders
    for i, folder in enumerate(sorted(set(folders))):
        tag = folder.lower().replace(" ", "-")
        if folder in user_colors:
            color_int = _hex_to_int(user_colors[folder])
        elif tag in user_colors:
            color_int = _hex_to_int(user_colors[tag])
        else:
            r, g, b = _PALETTE[i % len(_PALETTE)]
            color_int = _rgb_to_int(r, g, b)
        managed_tags[tag] = color_int

    # Also preserve colors for folders from prior runs that still have groups
    # (so re-running on a subset doesn't strip existing groups)
    old_groups: list[dict] = existing.get("colorGroups", [])
    user_groups: list[dict] = []
    for group in old_groups:
        q = group.get("query", "")
        if q.startswith("tag:#"):
            existing_tag = q[len("tag:#"):]
            if existing_tag in managed_tags:
                continue  # we'll re-write this one
            # Tag from a prior run not in this run's folders — keep it but
            # don't override the color so user customizations survive
        user_groups.append(group)

    # Build our groups in palette order (sorted by tag name for stability)
    our_groups = [
        _build_group(tag, color_int)
        for tag, color_int in sorted(managed_tags.items())
    ]

    # Merge: our groups first, then any user groups we don't manage
    all_groups = our_groups + user_groups

    # Preserve all other graph settings; only update colorGroups
    graph: dict = {
        "collapse-filter": existing.get("collapse-filter", True),
        "search": existing.get("search", ""),
        "showTags": existing.get("showTags", False),
        "showAttachments": existing.get("showAttachments", False),
        "hideUnresolved": existing.get("hideUnresolved", False),
        "showOrphans": existing.get("showOrphans", True),
        "collapse-color-groups": existing.get("collapse-color-groups", False),
        "colorGroups": all_groups,
        "collapse-display": existing.get("collapse-display", False),
        "showArrow": existing.get("showArrow", False),
        "textFadeMultiplier": existing.get("textFadeMultiplier", 0),
        "nodeSizeMultiplier": existing.get("nodeSizeMultiplier", 1),
        "lineSizeMultiplier": existing.get("lineSizeMultiplier", 1),
        "centerStrength": existing.get("centerStrength", 0.518713248970312),
        "repelStrength": existing.get("repelStrength", 10),
        "linkStrength": existing.get("linkStrength", 1),
        "linkDistance": existing.get("linkDistance", 250),
        "scale": existing.get("scale", 1),
        "close": existing.get("close", False),
    }

    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    print(f"  Obsidian graph groups: {len(our_groups)} folder(s) -> {graph_path}")
