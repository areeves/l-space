# l-space

A local-first audiobook library explorer and recommendation assistant. Use AI with your personal book library.

> Named after L-space, the Discworld idea that all libraries are connected through the
> books they hold. This is an unofficial fan project and is not affiliated with or endorsed
> by Terry Pratchett's estate or publishers.

## Status

Phase 1 scaffold. Working today: import of the Audible Library Extractor (ALE) CSV export,
normalization, SQLite storage, exact library queries (narrator counts, gaps in numbered
series, books in progress), a small FastAPI service, and a model provider boundary for a
local Ollama model. Recommendations and the natural-language layer are next.

## Design principles

- **Local first, free only.** Runs on your machine; the language model is served locally by
  [Ollama](https://ollama.com). No paid services or API keys.
- **Don't make an LLM do exact database work.** Counts, filters and series status are plain
  code over SQLite (`library/queries.py`). The model is reserved for fuzzy natural-language
  interpretation and for explaining results.
- **Inspectable recommendations (planned).** Recommendations will show the signals behind
  them (shared narrator, series, length, your rating) and let you correct them.
- **Originals preserved.** Every imported row is stored verbatim next to the cleaned fields.
- **Your data stays yours.** Real libraries go in `data/private/` (git-ignored). The repo
  ships only a small synthetic sample.

## Quick start

```bash
# Local Python
make install
make demo            # imports data/sample/library.csv and runs two exact queries
pytest

# API (http://localhost:8000/docs)
uvicorn l_space.main:app --reload

# Docker (app + Ollama)
make up
make pull-model      # one-time model download
```

### Your own library

l-space reads the CSV export of the
Audible Library Extractor browser extension (ALE). That is ALE's own
schema, not Goodreads'; the file only contains a "Search In Goodreads" link column.

```bash
l-space import data/private/my-library.csv
l-space series-gaps
```

Things the importer deals with, all covered by tests: lengths like `16h 54m `, progress as
`Finished` or `10h 48m left`, books in several series at once, unnumbered umbrella series
(`The Cosmere (book )`), decimal and omnibus positions (`book 22.5`, `book 1-7`), role
suffixes in credits (`Jane Doe - translator`), podcasts mixed in with books, and rows with no
store metadata. Ratings in the export are mostly Audible's community average; `My Rating` is
usually sparse, so recommendations can't lean on it alone.

## Layout

```text
src/l_space/
  api/         FastAPI routes
  core/        settings
  ingestion/   ALE CSV loading and normalization
  library/     models, SQLite store, deterministic queries
  models/      the only module that talks to a language model
tests/
evals/         fixed evaluation sets and scoring, kept outside the app code
data/sample/   synthetic demo library in the ALE schema
```

Folders such as `recommend/` or `workflows/` will be added when those features are real.

## Roadmap

1. Phase 1: import, normalize, exact queries, API (this scaffold)
2. Phase 2: natural-language questions routed to exact queries, with model-written explanations
3. Phase 3: explainable recommendations with correctable preferences, plus hand-judged evals
