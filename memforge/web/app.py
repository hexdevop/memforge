"""MemForge Web UI — FastAPI application."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from memforge.config import GLOBAL_ROOT, load_config
from memforge.core.storage import Store

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

def _markdown_filter(text: str) -> str:
    from markdown_it import MarkdownIt
    return MarkdownIt().render(text)


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["markdown"] = _markdown_filter


def _get_stores() -> tuple[Store | None, Store | None]:
    global_store = Store(GLOBAL_ROOT)
    project_store = Store(Path.cwd() / ".memforge")
    return (
        global_store if global_store.memory.exists() else None,
        project_store if project_store.memory.exists() else None,
    )


def create_app(token: str | None = None) -> FastAPI:
    app = FastAPI(title="MemForge", docs_url=None, redoc_url=None)

    # Static files
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Optional token auth middleware
    if token:
        from fastapi import HTTPException
        from starlette.middleware.base import BaseHTTPMiddleware

        class TokenMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if request.url.path.startswith("/static"):
                    return await call_next(request)
                if request.headers.get("X-Token") != token and request.query_params.get("token") != token:
                    raise HTTPException(status_code=401, detail="Unauthorized")
                return await call_next(request)

        app.add_middleware(TokenMiddleware)

    # Register routes
    from memforge.web.routes import api, views
    app.include_router(views.router)
    app.include_router(api.router, prefix="/api")

    return app


def run(host: str = "127.0.0.1", port: int = 7777, token: str | None = None) -> None:
    import uvicorn
    app = create_app(token=token)
    uvicorn.run(app, host=host, port=port, log_level="info")
