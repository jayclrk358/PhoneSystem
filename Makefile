# Common development tasks. Run "make help" for a list.

.PHONY: help setup lint format test test-integration test-ui build-ui dev check

help:
	@echo "make setup             install backend (venv) and web UI dependencies"
	@echo "make dev               run a local stack (private Asterisk + app on :8000)"
	@echo "make check             everything CI runs"
	@echo "make lint | format | test | test-integration | test-ui | build-ui"

setup:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
	cd frontend && npm ci

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run typecheck

format:
	cd backend && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

test:
	cd backend && .venv/bin/pytest -q

test-integration:
	cd backend && .venv/bin/pytest -q -m integration

test-ui:
	cd frontend && npm test

build-ui:
	cd frontend && npm run build

dev: build-ui
	cd backend && .venv/bin/python scripts/devstack.py

check: lint test test-integration test-ui build-ui
