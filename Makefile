.PHONY: install test lint up down pull-model demo eval eval-validate

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

up:
	docker compose up --build

down:
	docker compose down

pull-model:
	docker compose exec ollama ollama pull $${L_SPACE_OLLAMA_MODEL:-llama3.2}

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
