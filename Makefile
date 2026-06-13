.PHONY: help install lint format typecheck test test-unit test-integration migrate run paprika-refresh ui docker-up docker-down

help:
	@echo "Available targets:"
	@echo "  install          - install package + dev extras"
	@echo "  lint             - ruff check"
	@echo "  format           - ruff format"
	@echo "  typecheck        - mypy --strict"
	@echo "  test-unit        - run unit tests"
	@echo "  test-integration - run integration tests (needs Docker)"
	@echo "  test             - run all tests with coverage"
	@echo "  migrate          - alembic upgrade head"
	@echo "  run              - meal-planner run"
	@echo "  paprika-refresh  - sync newest Paprika HTML export + run full pipeline"
	@echo "  ui               - launch the Streamlit dashboard"
	@echo "  docker-up        - start postgres + pgadmin"
	@echo "  docker-down      - stop containers"

install:
	uv sync --all-extras 2>/dev/null || pip install -e ".[dev,report,llm-anthropic,llm-openai,serve]"

lint:
	ruff check src tests
	ruff format --check src tests

format:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy --strict src/meal_planner

test-unit:
	pytest -m unit -q

test-integration:
	pytest -m integration -q

test:
	pytest --cov=meal_planner --cov-report=term-missing --cov-report=xml

migrate:
	alembic upgrade head

run:
	meal-planner run

paprika-refresh:
	./scripts/paprika_refresh.sh

ui:
	meal-planner ui

docker-up:
	docker-compose --profile dev up -d postgres pgadmin

docker-down:
	docker-compose down
