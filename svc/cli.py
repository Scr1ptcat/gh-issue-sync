from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.github import GitHubClient
from app.schemas import IssueSpecList
from app.service import Orchestrator


async def run(mode: str, spec: IssueSpecList) -> dict:
    settings = get_settings()
    gh = GitHubClient(
        settings.gh_token, settings.request_timeout_seconds, settings.max_retries
    )
    try:
        orch = Orchestrator(gh)
        if mode == "validate":
            res = await orch.validate(spec)
        else:
            res = await orch.sync(spec)
        return json.loads(res.model_dump_json())
    finally:
        await gh.close()


def main() -> None:
    ap = argparse.ArgumentParser("svc.cli")
    ap.add_argument("mode", choices=["validate", "sync"])
    ap.add_argument("file", help="Path to IssueSpecList JSON")
    args = ap.parse_args()

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    spec = IssueSpecList.model_validate(data)

    out = asyncio.run(run(args.mode, spec))
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
