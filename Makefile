# Makefile for the AI Quant Platform (Task I1).
# Common dev / test / migrate / docker commands.
#
# - `make dev` / `make up`:  run backend + frontend via docker compose
# - `make dev-be` / `dev-fe`: run each app locally (needs local deps installed)
# - `make migrate`:           apply Alembic migrations against the configured DB
# - `make test-be`:           run backend pytest
# - `make lint`:              frontend production build (also type-checks TS)
#
# NOTE: the DB is external (Aliyun RDS) -- there is no `make db-up`.
# Copy backend/.env.example -> backend/.env (and a root .env for compose)
# and fill in real values before running anything that needs DB access.

.PHONY: help dev up down logs dev-be dev-fe migrate test test-be lint clean

help:
	@echo "AI Quant Platform - common commands:"
	@echo ""
	@echo "  make dev         start backend + frontend (docker compose, foreground)"
	@echo "  make up          same as 'dev' but detached (-d)"
	@echo "  make down        stop and remove containers"
	@echo "  make logs        tail compose logs"
	@echo ""
	@echo "  make dev-be      run backend locally (uvicorn --reload, needs backend/.venv)"
	@echo "  make dev-fe      run frontend dev server (vite, needs frontend/node_modules)"
	@echo "  make migrate     run alembic upgrade head (local backend venv)"
	@echo "  make test-be     run backend pytest (local backend venv)"
	@echo "  make lint        frontend production build (type-checks + bundles)"

# ---- docker compose targets ----

dev:
	docker compose up --build

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

# ---- local development targets ----

dev-be:
	cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000

dev-fe:
	cd frontend && npm run dev

# ---- database / tests / build ----

migrate:
	cd backend && source .venv/bin/activate && alembic upgrade head

test: test-be

test-be:
	cd backend && source .venv/bin/activate && pytest -q

lint:
	cd frontend && npm run build

clean:
	docker compose down -v
