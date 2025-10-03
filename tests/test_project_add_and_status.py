import httpx
import pytest
import respx

from app.github import GitHubClient
from app.schemas import IssueSpec, IssueSpecList
from app.service import Orchestrator


@pytest.mark.asyncio
async def test_project_add_and_status(tmp_path):
    gh = GitHubClient(token="", timeout=5, max_retries=1)
    orch = Orchestrator(gh)

    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.github.com/repos/org/repo/issues").mock(
            return_value=httpx.Response(200, json=[])
        )  # listing used by state fallback
        # Search exact title returns none -> create
        mock.get("https://api.github.com/search/issues").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        # Repo owner + viewer
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
                # _find_existing: get_issue_node_id not called yet; ensure call to create issue path
            ]
        )

        # Create issue
        mock.post("https://api.github.com/repos/org/repo/issues").mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 10,
                    "html_url": "https://github.com/org/repo/issues/10",
                },
            )
        )
        # After create: get issue node id + add to project + get item + update status
        mock.post("https://api.github.com/graphql").mock(
            side_effect=[
                httpx.Response(
                    200, json={"data": {"repository": {"issue": {"id": "ISSUE_NODE"}}}}
                ),  # get_issue_node_id
                httpx.Response(
                    200,
                    json={"data": {"addProjectV2ItemById": {"item": {"id": "ITEM10"}}}},
                ),  # add item
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "repository": {
                                "issue": {
                                    "id": "ISSUE_NODE",
                                    "projectItems": {
                                        "nodes": [
                                            {
                                                "id": "ITEM10",
                                                "project": {"id": "P1"},
                                                "fieldValues": {
                                                    "nodes": [
                                                        {
                                                            "__typename": "ProjectV2ItemFieldSingleSelectValue",
                                                            "field": {
                                                                "__typename": "ProjectV2SingleSelectField",
                                                                "name": "Status",
                                                            },
                                                            "name": None,
                                                            "optionId": None,
                                                        }
                                                    ]
                                                },
                                            }
                                        ]
                                    },
                                }
                            }
                        }
                    },
                ),  # fetch item
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "updateProjectV2ItemFieldValue": {
                                "projectV2Item": {"id": "ITEM10"}
                            }
                        }
                    },
                ),  # set status
            ]
        )

        spec = IssueSpecList(
            owner="org",
            repo="repo",
            project_title="ProjX",
            dry_run=False,
            items=[
                IssueSpec(
                    title="New Task",
                    summary="...",
                    labels=["area/db"],
                    depends_on=[],
                    estimate="S",
                    epic_label="epic/E2-Testing",
                )
            ],
        )
        rep = await orch.sync(spec)
        assert rep.created and rep.metrics.created == 1

    await gh.close()
