.PHONY: install test lint up down pull-model demo

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
