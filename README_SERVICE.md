# GitHub Issue Sync Service

**Purpose:** Stateless, idempotent FastAPI service to validate and sync GitHub Issues into a single Project (Projects v2). Uses REST for issues/labels and GraphQL for project discovery, item add, and status updates.

## Features
- Deterministic identity: exact title (trimmed, case‑sensitive) or stable slug (`lowercase-hyphenized(title)`), oldest match wins.
- Labels ensured (create if missing); never remove unrelated labels.
- Project item added only if missing; Status set to first available among: `To do`, `Todo`, `Not started`, `Backlog`.
- Works for Org or User projects; creates/reuses by title and computes accurate Project URL.
- DRY‑RUN support in `/validate` and `/sync`.
- Resilient retries with exponential backoff + jitter on 429/secondary‑rate‑limit and 5xx, honors `Retry-After`.
- ETag: `/issues` forwards `If-None-Match` to GitHub and returns `304` when unchanged.
- Structured JSON logs, request timing, minimal counters in reports.

## Configuration
Environment variables:
- `GH_TOKEN` (required) – GitHub token with `repo` + `project` scopes.
- `OWNER`, `REPO`, `PROJECT_TITLE` – defaults for `GET /issues` and examples.
- `LOG_LEVEL` – e.g., `INFO`.

## API
- `GET /health` → `{"status":"ok"}`
- `GET /issues?owner&repo&project_title&page&per_page` → Issues with optional project enrichment. Honors `If-None-Match`.
- `POST /validate` (`IssueSpecList`) → `ValidationReport`.
- `POST /sync` (`IssueSpecList`) → `SyncReport` (adds `project_url`).

## Schemas
See `openapi.yaml` and `app/schemas.py`.

## CLI
Run from JSON file (reuses the same code path):
```bash
python -m svc.cli validate examples/issues_spec.json
python -m svc.cli sync examples/issues_spec.json
```

## Assumptions & Notes
- `IssueSpec` supports either `epic_label` (preferred) **or** `epic_id` (`E1..E6`) which is mapped to labels as in the original script.
- When updating labels, we **add** missing labels (`POST /issues/{n}/labels`); we do not remove labels.
- Dependency links (`depends_on`) are recorded in issue body only; no cross‑issue linking is performed.
- Projects v2 GraphQL `fieldValues`/`projectItems` are used; if unavailable, status enrichment is skipped gracefully.
- Service is stateless; long‑term caching/ETag storage is not persisted between requests.

## Local Dev
```bash
make install
export GH_TOKEN=ghp_xxx
make run
```

## Security
- Tokens are never logged; `Authorization` headers are redacted.
- Timeouts and retries are bounded; unhandled GraphQL errors propagate with structured messages.

## Testing
- Unit tests mock HTTP (REST & GraphQL) with `respx`.
- Thin integration tests rely on recorded fixtures; see `tests/fixtures/`.
