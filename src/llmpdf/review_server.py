from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Any

from .review import ReviewProject


def create_review_app(project: ReviewProject) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as error:
        raise RuntimeError(
            "Review dependencies are missing; run `uv sync --extra review`"
        ) from error

    app = FastAPI(title="llmPDF Review", docs_url=None, redoc_url=None)

    @app.get("/api/project")
    def catalog() -> dict[str, Any]:
        return project.catalog()

    @app.get("/api/sources/{source_id}/tables/{table_id}")
    def table_detail(source_id: str, table_id: str) -> dict[str, Any]:
        try:
            with project.lock:
                return project.source(source_id).detail(table_id)
        except (KeyError, ValueError, OSError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/sources/{source_id}/comparison")
    def comparison(source_id: str) -> dict[str, Any]:
        try:
            with project.lock:
                return project.source(source_id).comparison()
        except (KeyError, ValueError, OSError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.put("/api/sources/{source_id}/tables/{table_id}/draft")
    def save_draft(
        source_id: str, table_id: str, decision: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            with project.lock:
                stored = project.source(source_id).save_draft(table_id, decision)
                return {"status": "saved", "draft": stored}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/sources/{source_id}/publish")
    def publish(source_id: str) -> dict[str, Any]:
        try:
            with project.lock:
                return project.source(source_id).publish()
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/sources/{source_id}/pdf")
    def pdf(source_id: str) -> FileResponse:
        try:
            source = project.source(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        path = source.pdf_path()
        if path is None:
            raise HTTPException(status_code=404, detail="Source PDF is unavailable")
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    @app.get("/api/sources/{source_id}/artifacts/{artifact_path:path}")
    def artifact(source_id: str, artifact_path: str) -> FileResponse:
        try:
            source = project.source(source_id)
            path = source.artifact_path(artifact_path)
        except (KeyError, ValueError, OSError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return FileResponse(path)

    static = Path(__file__).with_name("review_static")
    if not (static / "index.html").is_file():
        raise RuntimeError(
            "Review frontend is not built; run `npm ci && npm run build` in review-ui"
        )
    app.mount("/", StaticFiles(directory=static, html=True), name="review-ui")
    return app


def run_review_server(
    project: ReviewProject,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError(
            "Review dependencies are missing; run `uv sync --extra review`"
        ) from error
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(
        f"Review server: {url}\n"
        f"Loaded: {len(project.sources)} PDF(s), {project.catalog()['table_count']} table(s)"
    )
    uvicorn.run(create_review_app(project), host=host, port=port, log_level="info")
