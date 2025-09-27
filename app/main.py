from __future__ import annotations

import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .github import GitHubClient
from .logging import configure_logging, new_request_id, redact_headers
from .schemas import IssueSpecList, IssuesListResponse, SyncReport, ValidationReport
from .service import Orchestrator

app = FastAPI(title="GitHub Issue Sync Service", version="1.0.0")


@app.on_event("startup")
async def startup() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    rid = request.headers.get("x-request-id") or new_request_id()
    start = time.time()
    try:
        response: Response = await call_next(request)
    finally:
        dur = int((time.time() - start) * 1000)
        # Avoid logging bodies, redact auth
        app.logger = getattr(app, "logger", None)
        print(
            {
                "level": "INFO",
                "time_ms": dur,
                "request_id": rid,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
            }
        )
    response.headers["x-request-id"] = rid
    return response


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _effective(valuestr: Optional[str], fallback: str) -> str:
    return valuestr.strip() if valuestr and valuestr.strip() else fallback


@app.get("/issues", response_model=IssuesListResponse)
async def list_issues(
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    project_title: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    settings: Settings = Depends(get_settings),
):
    owner_eff = _effective(owner, settings.default_owner)
    repo_eff = _effective(repo, settings.default_repo)
    project_eff = project_title or settings.default_project_title

    gh = GitHubClient(settings.gh_token, settings.request_timeout_seconds, settings.max_retries)
    try:
        orch = Orchestrator(gh)
        resp = await orch.list_issues(owner_eff, repo_eff, project_eff, page, per_page, if_none_match)
        if resp.items == [] and resp.etag and if_none_match == resp.etag:
            # Upstream 304; honor
            return JSONResponse(status_code=304, content={"status": "not_modified"})
        return resp
    finally:
        await gh.close()


@app.post("/validate", response_model=ValidationReport)
async def validate(payload: IssueSpecList, settings: Settings = Depends(get_settings)):
    # per-request overrides are in the payload itself
    gh = GitHubClient(settings.gh_token, settings.request_timeout_seconds, settings.max_retries)
    try:
        orch = Orchestrator(gh)
        report = await orch.validate(payload)
        return report
    finally:
        await gh.close()


@app.post("/sync", response_model=SyncReport)
async def sync(payload: IssueSpecList, settings: Settings = Depends(get_settings)):
    gh = GitHubClient(settings.gh_token, settings.request_timeout_seconds, settings.max_retries)
    try:
        orch = Orchestrator(gh)
        report = await orch.sync(payload)
        return report
    finally:
        await gh.close()
