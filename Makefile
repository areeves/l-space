.PHONY: install test lint migrate migration up down pull-model demo eval eval-validate

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

# Create or upgrade the database schema (the app and CLI also do this on start)
migrate:
	l-space migrate

# Write a new migration from the changes in library/tables.py: make migration m="add users"
migration:
	alembic revision --autogenerate -m "$(m)"

up:
	docker compose up --build

down:
	docker compose down

pull-model:
	docker compose exec ollama ollama pull $${L_SPACE_OLLAMA_MODEL:-llama3.2}
	@if [ -n "$$L_SPACE_OLLAMA_ROUTER_MODEL" ]; then \
		docker compose exec ollama ollama pull $$L_SPACE_OLLAMA_ROUTER_MODEL; \
	fi

# Import the synthetic sample library and show a couple of exact queries
demo:
	l-space import data/sample/library.csv
	l-space series-gaps
	l-space narrators

# Check the eval dataset against the sample library (no model needed)
eval-validate:
	python evals/run.py --validate

# Run every eval question through the configured Ollama model
eval:
	python evals/run.py
