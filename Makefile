.PHONY: install run test lint fmt export-openapi docker-build docker-run

install:
	pip install --upgrade pip
	pip install -e .[dev]

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

test:
	pytest -q

lint:
	ruff check .

fmt:
	ruff format .

export-openapi:
	curl -s http://localhost:8080/openapi.json | yq -P > openapi.yaml

docker-build:
	docker build -t gh-issue-sync:latest .

docker-run:
	docker run --rm -e GH_TOKEN=$$GH_TOKEN -e OWNER=$$OWNER -e REPO=$$REPO -e PROJECT_TITLE="$$PROJECT_TITLE" -p 8080:8080 gh-issue-sync:latest
