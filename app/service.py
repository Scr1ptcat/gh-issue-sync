from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from .github import GitHubClient, GitHubError, slugify, STATUS_NAMES
from .schemas import (
    ErrorItem,
    IssueRecord,
    IssueSpec,
    IssueSpecList,
    IssuesListResponse,
    IssuesPage,
    Metrics,
    ReportItem,
    SyncReport,
    UpdatedChange,
    ValidationReport,
)

# Epic ID -> Label mapping (back-compat with provided bash/spec)
EPIC_MAP = {
    "E1": "epic/E1-Repo-Hygiene",
    "E2": "epic/E2-Testing",
    "E3": "epic/E3-Postgres",
    "E4": "epic/E4-Runtime",
    "E5": "epic/E5-Observability-Security",
    "E6": "epic/E6-Models-Docs",
}


def apply_epic(spec: IssueSpec) -> IssueSpec:
    if spec.epic_label:
        return spec
    if spec.epic_id and spec.epic_id in EPIC_MAP:
        spec.epic_label = EPIC_MAP[spec.epic_id]
    return spec


def build_issue_body(spec: IssueSpec, project_title: str) -> str:
    parts = [
        f"Summary: {spec.summary}",
        f"Epic: {spec.epic_label or ''}".rstrip(),
        f"Depends on: {', '.join(spec.depends_on) if spec.depends_on else ''}".rstrip(),
        f"Estimate: {spec.estimate or ''}".rstrip(),
        f"Project: {project_title}",
    ]
    return "\n\n".join(parts)


class Orchestrator:
    def __init__(self, gh: GitHubClient):
        self.gh = gh

    # ---------- Issues listing (with optional project enrichment) ----------

    async def list_issues(
        self, owner: str, repo: str, project_title: Optional[str], page: int, per_page: int, etag: Optional[str]
    ) -> IssuesListResponse:
        issues, resp_etag, has_next = await self.gh.list_issues(owner, repo, page, per_page, if_none_match=etag)
        if resp_etag and issues == [] and not has_next:
            # Upstream 304 -> bubble through as empty with etag (caller should set 304)
            return IssuesListResponse(
                owner=owner,
                repo=repo,
                project_title=project_title,
                etag=resp_etag,
                pagination=IssuesPage(page=page, per_page=per_page, has_next=False, next_page=None),
                items=[],
            )

        # Optionally map: project_item_id and status_option for a given project
        project_id = None
        status_field_id = None
        if project_title:
            project_id, _, _ = await self.ensure_project(owner, repo, project_title, create_if_missing=False)
            if project_id:
                status_field_id, _, _ = await self.gh.get_status_field_and_option(project_id)

        records: List[IssueRecord] = []

        async def enrich(item: Dict[str, Any]) -> IssueRecord:
            labels = [l["name"] for l in item.get("labels", [])] if isinstance(item.get("labels"), list) else []
            project_item_id = None
            status_name = None
            if project_id:
                try:
                    pid, sname = await self.gh.get_issue_project_item_and_status(owner, repo, item["number"], project_id)
                    project_item_id, status_name = pid, sname
                except GitHubError:
                    # Non-fatal for listing
                    pass
            return IssueRecord(
                number=int(item["number"]),
                url=item["html_url"],
                title=item["title"],
                labels=labels,
                state=item["state"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                project_item_id=project_item_id,
                status_option=status_name,
            )

        # limit concurrency
        sem = asyncio.Semaphore(10)

        async def guarded(item: Dict[str, Any]) -> IssueRecord:
            async with sem:
                return await enrich(item)

        enriched = await asyncio.gather(*(guarded(i) for i in issues))
        next_page = page + 1 if has_next else None

        return IssuesListResponse(
            owner=owner,
            repo=repo,
            project_title=project_title,
            etag=resp_etag,
            pagination=IssuesPage(page=page, per_page=per_page, has_next=has_next, next_page=next_page),
            items=enriched,
        )

    # ---------- Validation / Sync ----------

    async def validate(self, spec_list: IssueSpecList) -> ValidationReport:
        start = time.time()
        report = ValidationReport(owner=spec_list.owner, repo=spec_list.repo, project_title=spec_list.project_title)
        project_id, project_url, _ = await self.ensure_project(
            spec_list.owner, spec_list.repo, spec_list.project_title, create_if_missing=True if spec_list.dry_run else False
        )
        report.project_url = project_url

        for spec in spec_list.items:
            spec = apply_epic(spec)
            desired_labels = sorted(set(spec.labels + ([spec.epic_label] if spec.epic_label else [])))
            try:
                state = await self._compute_state(spec_list.owner, spec_list.repo, project_id, spec)
            except Exception as e:
                report.errors.append(ErrorItem(title=spec.title, reason="lookup_failed", detail=str(e)))
                continue

            if state["existing"] is None:
                report.created.append(ReportItem(title=spec.title, number=0, url=""))
            else:
                changes = []
                if not state["labels_satisfied"]:
                    changes.append("labels")
                if not state["in_project"]:
                    changes.append("project_item")
                if state["needs_status_update"]:
                    changes.append("status")
                if not changes:
                    report.unchanged.append(
                        ReportItem(title=spec.title, number=state["existing"]["number"], url=state["existing"]["html_url"])
                    )
                else:
                    report.updated.append(
                        UpdatedChange(
                            title=spec.title,
                            number=state["existing"]["number"],
                            url=state["existing"]["html_url"],
                            changes=changes,
                        )
                    )

        total = len(spec_list.items)
        report.metrics = Metrics(
            total=total,
            created=len(report.created),
            updated=len(report.updated),
            unchanged=len(report.unchanged),
            errors=len(report.errors),
            duration_ms=int((time.time() - start) * 1000),
        )
        return report

    async def sync(self, spec_list: IssueSpecList) -> SyncReport:
        start = time.time()
        report = SyncReport(owner=spec_list.owner, repo=spec_list.repo, project_title=spec_list.project_title)
        project_id, project_url, status_field_info = await self.ensure_project(
            spec_list.owner, spec_list.repo, spec_list.project_title, create_if_missing=True
        )
        report.project_url = project_url
        status_field_id, todo_opt_id, _ = status_field_info

        for spec in spec_list.items:
            spec = apply_epic(spec)
            desired_labels = sorted(set(spec.labels + ([spec.epic_label] if spec.epic_label else [])))

            try:
                state = await self._compute_state(spec_list.owner, spec_list.repo, project_id, spec)
            except Exception as e:
                report.errors.append(ErrorItem(title=spec.title, reason="lookup_failed", detail=str(e)))
                continue

            created_item = None
            updated_changes: List[str] = []
            try:
                # ensure labels (idempotent)
                if not spec_list.dry_run:
                    for lb in desired_labels:
                        await self.gh.ensure_label(spec_list.owner, spec_list.repo, lb)

                if state["existing"] is None:
                    if spec_list.dry_run:
                        report.created.append(ReportItem(title=spec.title, number=0, url=""))
                        continue
                    body = build_issue_body(spec, spec_list.project_title)
                    created = await self.gh.create_issue(spec_list.owner, spec_list.repo, spec.title, body, desired_labels)
                    created_item = created
                    # add to project
                    issue_node_id = await self.gh.get_issue_node_id(spec_list.owner, spec_list.repo, created["number"])
                    if project_id and issue_node_id:
                        await self.gh.add_item_to_project(project_id, issue_node_id)
                        # fetch item id to set status
                        item_id, cur_status = await self.gh.get_issue_project_item_and_status(
                            spec_list.owner, spec_list.repo, created["number"], project_id
                        )
                        if item_id and status_field_id and todo_opt_id:
                            await self.gh.update_item_status(project_id, item_id, status_field_id, todo_opt_id)
                    report.created.append(ReportItem(title=spec.title, number=created["number"], url=created["html_url"]))
                    continue  # next spec

                # existing: apply additive labels
                if not state["labels_satisfied"]:
                    if spec_list.dry_run:
                        updated_changes.append("labels")
                    else:
                        missing = sorted(set(desired_labels) - set(state["existing_labels"]))
                        if missing:
                            await self.gh.add_labels(spec_list.owner, spec_list.repo, state["existing"]["number"], missing)
                            updated_changes.append("labels")

                # project item add
                if project_id and not state["in_project"]:
                    if spec_list.dry_run:
                        updated_changes.append("project_item")
                    else:
                        issue_node_id = await self.gh.get_issue_node_id(spec_list.owner, spec_list.repo, state["existing"]["number"])
                        if issue_node_id:
                            await self.gh.add_item_to_project(project_id, issue_node_id)
                            updated_changes.append("project_item")

                # status set
                if project_id and state["in_project"] and state["needs_status_update"] and status_field_id and todo_opt_id:
                    if spec_list.dry_run:
                        updated_changes.append("status")
                    else:
                        item_id = state["project_item_id"]
                        if item_id:
                            await self.gh.update_item_status(project_id, item_id, status_field_id, todo_opt_id)
                            updated_changes.append("status")

                if not updated_changes:
                    report.unchanged.append(
                        ReportItem(
                            title=spec.title,
                            number=state["existing"]["number"],
                            url=state["existing"]["html_url"],
                        )
                    )
                else:
                    report.updated.append(
                        UpdatedChange(
                            title=spec.title,
                            number=state["existing"]["number"],
                            url=state["existing"]["html_url"],
                            changes=updated_changes,
                        )
                    )
            except GitHubError as e:
                report.errors.append(
                    ErrorItem(title=spec.title, reason="github_error", detail=f"{e.status}: {str(e)}")
                )

        total = len(spec_list.items)
        report.metrics = Metrics(
            total=total,
            created=len(report.created),
            updated=len(report.updated),
            unchanged=len(report.unchanged),
            errors=len(report.errors),
            duration_ms=int((time.time() - start) * 1000),
        )
        return report

    # ---------- Helpers ----------

    async def ensure_project(
        self, owner: str, repo: str, project_title: str, *, create_if_missing: bool
    ) -> Tuple[Optional[str], Optional[str], Tuple[Optional[str], Optional[str], dict]]:
        owner_id, owner_typename, viewer_id, viewer_login = await self.gh.get_repo_owner_and_viewer(owner, repo)
        # Find
        projects = await self.gh.list_projects_for_node(owner_id)
        project_id = None
        number = None
        for p in projects:
            if p["title"] == project_title:
                project_id = p["id"]
                number = int(p["number"])
                break
        # Create if missing
        if not project_id and create_if_missing:
            # Try owner then fallback to viewer
            try:
                project_id, number = await self.gh.create_project(owner_id, project_title)
            except GitHubError:
                project_id, number = await self.gh.create_project(viewer_id, project_title)

        project_url = None
        if project_id:
            otype, ologin, num = await self.gh.get_project_meta(project_id)
            if otype == "Organization":
                project_url = f"https://github.com/orgs/{ologin}/projects/{num}"
            elif otype == "User":
                project_url = f"https://github.com/users/{ologin}/projects/{num}"
            else:
                project_url = f"https://github.com/orgs/{owner}/projects/{num}"
        status_field_id, todo_opt_id, opts = (None, None, {})
        if project_id:
            status_field_id, todo_opt_id, opts = await self.gh.get_status_field_and_option(project_id)
        return project_id, project_url, (status_field_id, todo_opt_id, opts)

    async def _find_existing(self, owner: str, repo: str, title: str) -> Optional[Dict[str, Any]]:
        # Exact title search
        s = await self.gh.search_issues_exact_title(owner, repo, title)
        candidates = list(s)
        # Slug fallback: scan first 5 pages of issues (state=all) to reduce cost
        if not candidates:
            target = slugify(title)
            for page in range(1, 6):
                lst, _, has_next = await self.gh.list_issues(owner, repo, page, per_page=100)
                for it in lst:
                    if "pull_request" in it:
                        continue
                    if slugify(it["title"]) == target:
                        candidates.append({"number": it["number"], "html_url": it["html_url"], "title": it["title"], "created_at": it["created_at"]})
                if not has_next:
                    break

        if not candidates:
            return None
        # Choose oldest by createdAt/created_at
        def created_at(c: Dict[str, Any]) -> str:
            return c.get("created_at") or c.get("createdAt") or ""

        candidates.sort(key=lambda c: created_at(c))
        # Hydrate to full issue for consistent shape
        chosen = candidates[0]
        full = await self.gh.get_issue(owner, repo, int(chosen["number"]))
        return full

    async def _compute_state(
        self, owner: str, repo: str, project_id: Optional[str], spec: IssueSpec
    ) -> Dict[str, Any]:
        desired_labels = sorted(set(spec.labels + ([spec.epic_label] if spec.epic_label else [])))
        existing = await self._find_existing(owner, repo, spec.title)
        if existing is None:
            return {
                "existing": None,
                "existing_labels": [],
                "in_project": False,
                "project_item_id": None,
                "current_status": None,
                "needs_status_update": False,
                "desired_labels": desired_labels,
                "labels_satisfied": False,
            }
        labels = [l["name"] for l in existing.get("labels", [])] if isinstance(existing.get("labels"), list) else []
        in_project = False
        project_item_id = None
        current_status = None
        if project_id:
            try:
                pid, sname = await self.gh.get_issue_project_item_and_status(owner, repo, existing["number"], project_id)
                project_item_id, current_status = pid, sname
                in_project = pid is not None
            except GitHubError:
                in_project = False

        # Status needs update if either not present or not one of the initial desired options
        needs_status_update = in_project and (current_status not in STATUS_NAMES)
        labels_satisfied = set(desired_labels).issubset(set(labels))

        return {
            "existing": existing,
            "existing_labels": labels,
            "in_project": in_project,
            "project_item_id": project_item_id,
            "current_status": current_status,
            "needs_status_update": needs_status_update,
            "desired_labels": desired_labels,
            "labels_satisfied": labels_satisfied,
        }
