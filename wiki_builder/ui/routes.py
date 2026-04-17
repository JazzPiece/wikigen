"""
ui/routes.py — Route handlers.

All routes are stubs — they return the rendered template with placeholder context.
Wire up real operations (ingest, query, lint, status) as needed.
"""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import WikiConfig
from ..state import WikiState


def register_routes(app: FastAPI, cfg: WikiConfig, templates: Jinja2Templates) -> None:

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        state = WikiState(cfg.wiki_path())
        state.load()
        wiki_root = cfg.wiki_path()
        total_articles = sum(1 for _ in wiki_root.rglob("*.md")) if wiki_root.exists() else 0
        return templates.TemplateResponse("index.html", {
            "request": request,
            "project_name": cfg.project.name,
            "source_path": str(cfg.source_path()),
            "wiki_path": str(cfg.wiki_path()),
            "llm_backend": cfg.llm.backend,
            "llm_model": cfg.llm.model,
            "total_articles": total_articles,
            "total_tracked": len(state.all_source_keys()),
        })

    @app.get("/ingest", response_class=HTMLResponse)
    async def ingest_page(request: Request):
        return templates.TemplateResponse("ingest.html", {
            "request": request,
            "project_name": cfg.project.name,
            "result": None,
        })

    @app.post("/ingest", response_class=HTMLResponse)
    async def ingest_run(
        request: Request,
        incremental: bool = Form(True),
        no_llm: bool = Form(False),
        no_crossref: bool = Form(False),
    ):
        # TODO: run operations.ingest.run_ingest, stream output via SSE or capture to string
        return templates.TemplateResponse("ingest.html", {
            "request": request,
            "project_name": cfg.project.name,
            "result": {"status": "not_implemented"},
        })

    @app.get("/query", response_class=HTMLResponse)
    async def query_page(request: Request):
        return templates.TemplateResponse("query.html", {
            "request": request,
            "project_name": cfg.project.name,
            "question": "",
            "answer": None,
        })

    @app.post("/query", response_class=HTMLResponse)
    async def query_run(request: Request, question: str = Form(...)):
        # TODO: run operations.query.run_query
        return templates.TemplateResponse("query.html", {
            "request": request,
            "project_name": cfg.project.name,
            "question": question,
            "answer": None,
        })

    @app.get("/lint", response_class=HTMLResponse)
    async def lint_page(request: Request):
        # TODO: run operations.lint.run_lint and pass report
        return templates.TemplateResponse("lint.html", {
            "request": request,
            "project_name": cfg.project.name,
            "report": None,
        })
