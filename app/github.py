from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

SECONDARY_LIMIT_SUBSTR = "secondary rate limit"
GITHUB_API = "https://api.github.com"
GITHUB_GQL = "https://api.github.com/graphql"

DEFAULT_LABEL_COLOR = "ededed"

STATUS_NAMES = ["To do", "Todo", "Not started", "Backlog"]


def slugify(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t)
    t = re.sub(r"[\s_]+", "-", t)
    return re.sub(r"-{2,}", "-", t).strip("-")


class GitHubError(Exception):
    def __init__(
        self, message: str, status: int | None = None, body: Any | None = None
    ):
        super().__init__(message)
        self.status = status
        self.body = body


class GitHubClient:
    def __init__(
        self,
        token: str,
        timeout: float = 20.0,
        max_retries: int = 5,
        request_id: str | None = None,
    ):
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        limits = httpx.Limits(max_connections=20, max_keepalive_connections=20)
        self.client = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers=headers,
            timeout=timeout,
            http2=True,
            limits=limits,
        )
        self.gql = httpx.AsyncClient(
            base_url=GITHUB_GQL,
            headers=headers,
            timeout=timeout,
            http2=True,
            limits=limits,
        )
        self.max_retries = max_retries
        self.request_id = request_id or "req-unknown"

    # ------------- Retry / Backoff -------------

    async def _with_retries(self, fn, *args, **kwargs) -> httpx.Response:
        attempt = 0
        delay = 1.0
        while True:
            try:
                resp: httpx.Response = await fn(*args, **kwargs)
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                if attempt >= self.max_retries:
                    raise
            else:
                retry_after = None
                if resp.status_code in (429, 502, 503, 504) or (
                    resp.status_code == 403
                    and SECONDARY_LIMIT_SUBSTR in resp.text.lower()
                ):
                    retry_after = resp.headers.get("retry-after")
                if resp.status_code < 500 and resp.status_code not in (429, 403):
                    return resp
                if (
                    retry_after is None
                    and resp.status_code == 403
                    and SECONDARY_LIMIT_SUBSTR not in resp.text.lower()
                ):
                    return resp  # hard 403
                if attempt >= self.max_retries:
                    return resp
            attempt += 1
            base = float(retry_after) if retry_after else delay
            jitter = random.uniform(0, 0.5)
            await asyncio.sleep(base + jitter)
            delay = min(delay * 2, 16.0)

    # ------------- REST -------------

    async def rest(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: Dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self._with_retries(
            self.client.request,
            method,
            path,
            params=params,
            json=json_body,
            headers=headers,
        )

    async def list_issues(
        self,
        owner: str,
        repo: str,
        page: int,
        per_page: int,
        if_none_match: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        headers = {"If-None-Match": if_none_match} if if_none_match else None
        resp = await self.rest(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            params={"state": "all", "per_page": per_page, "page": page},
            headers=headers,
        )
        if resp.status_code == 304:
            return [], resp.headers.get("ETag"), False
        if resp.status_code >= 400:
            raise GitHubError(
                f"list_issues failed: {resp.text}", resp.status_code, resp.text
            )
        etag = resp.headers.get("ETag")
        data = resp.json()
        # filter out PRs
        issues = [i for i in data if "pull_request" not in i]
        has_next = False
        link = resp.headers.get("Link")
        if link and 'rel="next"' in link:
            has_next = True
        return issues, etag, has_next

    async def search_issues_exact_title(
        self, owner: str, repo: str, title: str
    ) -> List[Dict[str, Any]]:
        q = f'repo:{owner}/{repo} type:issue in:title "{title}"'
        params = {"q": q, "per_page": 100, "sort": "created", "order": "asc"}
        resp = await self.rest("GET", "/search/issues", params=params)
        if resp.status_code >= 400:
            raise GitHubError(
                f"search_issues failed: {resp.text}", resp.status_code, resp.text
            )
        items = [i for i in resp.json().get("items", []) if i.get("title") == title]
        return items

    async def get_issue(self, owner: str, repo: str, number: int) -> Dict[str, Any]:
        resp = await self.rest("GET", f"/repos/{owner}/{repo}/issues/{number}")
        if resp.status_code >= 400:
            raise GitHubError(
                f"get_issue failed: {resp.text}", resp.status_code, resp.text
            )
        return resp.json()

    async def create_issue(
        self, owner: str, repo: str, title: str, body: str, labels: List[str]
    ) -> Dict[str, Any]:
        resp = await self.rest(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json_body={"title": title, "body": body, "labels": labels},
        )
        if resp.status_code >= 400:
            raise GitHubError(
                f"create_issue failed: {resp.text}", resp.status_code, resp.text
            )
        return resp.json()

    async def add_labels(
        self, owner: str, repo: str, number: int, labels: List[str]
    ) -> None:
        if not labels:
            return
        resp = await self.rest(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/labels",
            json_body={"labels": labels},
        )
        if resp.status_code >= 400:
            raise GitHubError(
                f"add_labels failed: {resp.text}", resp.status_code, resp.text
            )

    async def ensure_label(
        self,
        owner: str,
        repo: str,
        name: str,
        color: str = DEFAULT_LABEL_COLOR,
        desc: str = "",
    ) -> None:
        enc = quote(name, safe="")
        get = await self.rest("GET", f"/repos/{owner}/{repo}/labels/{enc}")
        if get.status_code == 200:
            return
        if get.status_code != 404:
            raise GitHubError(
                f"label ensure failed: {get.text}", get.status_code, get.text
            )
        create = await self.rest(
            "POST",
            f"/repos/{owner}/{repo}/labels",
            json_body={"name": name, "color": color, "description": desc},
        )
        if create.status_code >= 400:
            raise GitHubError(
                f"label create failed: {create.text}", create.status_code, create.text
            )

    # ------------- GraphQL -------------

    async def graphql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._with_retries(
            self.gql.post, "", json={"query": query, "variables": variables}
        )
        # GraphQL can return 200 with errors
        data = resp.json()
        if "errors" in data and data["errors"]:
            msg = json.dumps(data["errors"])
            if SECONDARY_LIMIT_SUBSTR in msg.lower():
                # Let caller retry loop handle as error
                raise GitHubError(
                    f"graphql rate-limited: {msg}", resp.status_code, data
                )
            raise GitHubError(f"graphql error: {msg}", resp.status_code, data)
        if resp.status_code >= 400:
            raise GitHubError(
                f"graphql http error: {resp.text}", resp.status_code, resp.text
            )
        return data["data"]

    async def get_repo_owner_and_viewer(
        self, owner: str, repo: str
    ) -> Tuple[str, str, str, str]:
        q = """
        query($owner:String!,$repo:String!){
          repository(owner:$owner,name:$repo){ id owner{ id login __typename } }
          viewer{ id login }
        }"""
        d = await self.graphql(q, {"owner": owner, "repo": repo})
        repo_owner = d["repository"]["owner"]
        return (
            repo_owner["id"],
            repo_owner["__typename"],
            d["viewer"]["id"],
            d["viewer"]["login"],
        )

    async def list_projects_for_node(
        self, owner_id: str, first: int = 100
    ) -> List[Dict[str, Any]]:
        q = """
        query($id:ID!,$first:Int!){
          node(id:$id){
            ... on Organization { projectsV2(first:$first){ nodes{ id title number } } }
            ... on User { projectsV2(first:$first){ nodes{ id title number } } }
          }
        }"""
        d = await self.graphql(q, {"id": owner_id, "first": first})
        node = d.get("node") or {}
        projects = (node.get("projectsV2") or {}).get("nodes") or []
        return projects

    async def create_project(self, owner_id: str, title: str) -> Tuple[str, int]:
        m = """
        mutation($ownerId:ID!,$title:String!){
          createProjectV2(input:{ownerId:$ownerId,title:$title}){ projectV2{ id number title } }
        }"""
        d = await self.graphql(m, {"ownerId": owner_id, "title": title})
        p = d["createProjectV2"]["projectV2"]
        return p["id"], int(p["number"])

    async def get_project_meta(self, project_id: str) -> Tuple[str, str, int]:
        q = """
        query($pid:ID!){
          node(id:$pid){
            ... on ProjectV2 { number title owner { __typename ... on Organization { login } ... on User { login } } }
          }
        }"""
        d = await self.graphql(q, {"pid": project_id})
        node = d["node"]
        owner = node["owner"]
        return owner["__typename"], owner["login"], int(node["number"])

    async def get_status_field_and_option(
        self, project_id: str
    ) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
        q = """
        query($pid:ID!){
          node(id:$pid){
            ... on ProjectV2 {
              fields(first:50){
                nodes{
                  __typename
                  ... on ProjectV2SingleSelectField { id name options{ id name } }
                  ... on ProjectV2FieldCommon { id name }
                }
              }
            }
          }
        }"""
        d = await self.graphql(q, {"pid": project_id})
        nodes = d["node"]["fields"]["nodes"]
        status_field_id = None
        todo_opt_id = None
        options_map: Dict[str, str] = {}
        for n in nodes:
            if (
                n.get("__typename") == "ProjectV2SingleSelectField"
                and n.get("name") == "Status"
            ):
                status_field_id = n["id"]
                for opt in n.get("options") or []:
                    options_map[opt["name"]] = opt["id"]
                for want in STATUS_NAMES:
                    if want in options_map:
                        todo_opt_id = options_map[want]
                        break
        return status_field_id, todo_opt_id, options_map

    async def get_issue_node_id(
        self, owner: str, repo: str, number: int
    ) -> Optional[str]:
        q = """
        query($owner:String!,$repo:String!,$num:Int!){
          repository(owner:$owner,name:$repo){ issue(number:$num){ id } }
        }"""
        d = await self.graphql(q, {"owner": owner, "repo": repo, "num": number})
        issue = (d.get("repository") or {}).get("issue")
        return issue["id"] if issue else None

    async def add_item_to_project(
        self, project_id: str, content_id: str
    ) -> Optional[str]:
        m = """
        mutation($pid:ID!,$cid:ID!){
          addProjectV2ItemById(input:{projectId:$pid,contentId:$cid}){ item{ id } }
        }"""
        try:
            d = await self.graphql(m, {"pid": project_id, "cid": content_id})
        except GitHubError as e:
            # if already exists, GraphQL often returns an error; treat as idempotent
            if e.status == 200 and "already" in str(e).lower():
                return None
            raise
        item = d["addProjectV2ItemById"]["item"]
        return item["id"] if item else None

    async def update_item_status(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        m = """
        mutation($pid:ID!,$iid:ID!,$fid:ID!,$opt:String!){
          updateProjectV2ItemFieldValue(input:{
            projectId:$pid, itemId:$iid, fieldId:$fid, value:{singleSelectOptionId:$opt}
          }){ projectV2Item{ id } }
        }"""
        await self.graphql(
            m, {"pid": project_id, "iid": item_id, "fid": field_id, "opt": option_id}
        )

    async def get_issue_project_item_and_status(
        self, owner: str, repo: str, number: int, project_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        q = """
        query($owner:String!,$repo:String!,$num:Int!,$pid:ID!){
          repository(owner:$owner,name:$repo){
            issue(number:$num){
              id
              projectItems(first:20){
                nodes{
                  id
                  project{ id }
                  fieldValues(first:50){
                    nodes{
                      __typename
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        field{ __typename ... on ProjectV2SingleSelectField { name } }
                        name
                        optionId
                      }
                    }
                  }
                }
              }
            }
          }
        }"""
        d = await self.graphql(
            q, {"owner": owner, "repo": repo, "num": number, "pid": project_id}
        )
        issue = (d.get("repository") or {}).get("issue") or {}
        items = ((issue.get("projectItems") or {}).get("nodes")) or []
        for it in items:
            proj = it.get("project") or {}
            if proj.get("id") == project_id:
                # find status name if present
                status_name = None
                fvs = (it.get("fieldValues") or {}).get("nodes") or []
                for fv in fvs:
                    if fv.get("__typename") == "ProjectV2ItemFieldSingleSelectValue":
                        field = fv.get("field") or {}
                        if field.get("__typename") == "ProjectV2SingleSelectField":
                            if field.get("name") == "Status":
                                status_name = fv.get("name")
                                break
                return it.get("id"), status_name
        return None, None

    # ------------- Utilities -------------

    async def close(self) -> None:
        await asyncio.gather(self.client.aclose(), self.gql.aclose())
