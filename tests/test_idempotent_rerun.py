import httpx
import pytest
import respx

from app.github import GitHubClient
from app.schemas import IssueSpec, IssueSpecList
from app.service import Orchestrator


@pytest.mark.asyncio
async def test_rerun_unchanged():
    gh = GitHubClient(token="", timeout=5, max_retries=1)
    orch = Orchestrator(gh)

    with respx.mock(assert_all_called=False) as mock:
        issue_items_graphql = {
            "data": {
                "repository": {
                    "issue": {
                        "id": "ISSNODE",
                        "projectItems": {
                            "nodes": [
                                {
                                    "id": "ITEM42",
                                    "project": {"id": "P1"},
                                    "fieldValues": {
                                        "nodes": [
                                            {
                                                "__typename": "ProjectV2ItemFieldSingleSelectValue",
                                                "field": {
                                                    "__typename": "ProjectV2SingleSelectField",
                                                    "name": "Status",
                                                },
                                                "name": "To do",
                                                "optionId": "S1",
                                            }
                                        ]
                                    },
                                }
                            ]
                        },
                    }
                }
            }
        }
        # Search finds existing issue with exact title
        mock.get("https://api.github.com/search/issues").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "number": 42,
                            "html_url": "x",
                            "title": "Same",
                            "created_at": "2024-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        )
        # Hydrate get_issue
        mock.get("https://api.github.com/repos/org/repo/issues/42").mock(
            return_value=httpx.Response(
                200,
                json={
                    "number": 42,
                    "html_url": "x",
                    "title": "Same",
                    "labels": [{"name": "area/db"}, {"name": "epic/E3-Postgres"}],
                },
            )
        )
        # Repo owner + viewer, project lookup, meta, status field
        mock.post("https://api.github.com/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "repository": {
                                "id": "R1",
                                "owner": {
                                    "id": "O1",
                                    "login": "org",
                                    "__typename": "Organization",
                                },
                            },
                            "viewer": {"id": "U1", "login": "me"},
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "node": {
                                "projectsV2": {
                                    "nodes": [
                                        {"id": "P1", "title": "ProjX", "number": 5}
                                    ]
                                }
                            }
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "node": {
                                "number": 5,
                                "title": "ProjX",
                                "owner": {"__typename": "Organization", "login": "org"},
                            }
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "node": {
                                "fields": {
                                    "nodes": [
                                        {
                                            "__typename": "ProjectV2SingleSelectField",
                                            "id": "F1",
                                            "name": "Status",
                                            "options": [{"id": "S1", "name": "To do"}],
                                        }
                                    ]
                                }
                            }
                        }
                    },
                ),
                httpx.Response(200, json=issue_items_graphql),
            ]
        )

        spec = IssueSpecList(
            owner="org",
            repo="repo",
            project_title="ProjX",
            dry_run=True,
            items=[
                IssueSpec(
                    title="Same",
                    summary="...",
                    labels=["area/db"],
                    epic_label="epic/E3-Postgres",
                )
            ],
        )
        rep = await orch.validate(spec)
        assert rep.unchanged and rep.metrics.unchanged == 1
        assert not rep.updated and not rep.created

    await gh.close()
