import httpx
import pytest
import respx

from app.github import GitHubClient


@pytest.mark.asyncio
async def test_ensure_label_create():
    gh = GitHubClient(token="", timeout=5, max_retries=1)
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/org/repo/labels/newlabel").mock(
            return_value=httpx.Response(404)
        )
        created = mock.post("/repos/org/repo/labels").mock(
            return_value=httpx.Response(201, json={"name": "newlabel"})
        )
        await gh.ensure_label("org", "repo", "newlabel")
        assert created.called
    await gh.close()


@pytest.mark.asyncio
async def test_ensure_label_exists():
    gh = GitHubClient(token="", timeout=5, max_retries=1)
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/org/repo/labels/exist").mock(
            return_value=httpx.Response(200, json={"name": "exist"})
        )
        created = mock.post("/repos/org/repo/labels").mock(
            return_value=httpx.Response(201)
        )
        await gh.ensure_label("org", "repo", "exist")
        assert not created.called
    await gh.close()
