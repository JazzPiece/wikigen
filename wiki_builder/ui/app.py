"""
ui/app.py — FastAPI application factory.

Start with: wikigen serve
Or directly: uvicorn wiki_builder.ui.app:create_app --factory --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import WikiConfig

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(cfg: WikiConfig) -> FastAPI:
    app = FastAPI(title="wikigen", version="0.1.0")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from .routes import register_routes
    register_routes(app, cfg, templates)

    return app
