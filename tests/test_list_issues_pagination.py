import json
import respx
import httpx
import pytest
from app.github import GitHubClient
from app.service import Orchestrator


@pytest.mark.asyncio
async def test_list_issues_pagination(tmp_path):
    gh = GitHubClient(token="", timeout=5, max_retries=1)

    with respx.mock(base_url="https://api.github.com") as mock:
        # Page 1 with Link header to next
        r1 = mock.get("/repos/org/repo/issues").mock(
            return_value=httpx.Response(
                200,
                json=json.loads((tmp_path / "issues_page_1.json").read_text()) if False else [
                    {
                        "number": 1,
                        "html_url": "https://github.com/org/repo/issues/1",
                        "title": "A",
                        "labels": [{"name":"x"}],
                        "state":"open",
                        "created_at":"2024-01-01T00:00:00Z",
                        "updated_at":"2024-01-01T01:00:00Z"
                    }
                ],
                headers={"Link": '<https://api.github.com/...&page=2>; rel="next"', "ETag": '"etag1"'}
            )
        )
        orch = Orchestrator(gh)
        resp = await orch.list_issues("org", "repo", None, page=1, per_page=50, etag=None)
        assert resp.pagination.has_next is True
        assert len(resp.items) == 1
        assert resp.items[0].number == 1

    await gh.close()
