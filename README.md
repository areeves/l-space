# l-space

A local-first audiobook library explorer and recommendation assistant. Use AI with your personal book library.

> Named after L-space, the Discworld idea that all libraries are connected through the
> books they hold. This is an unofficial fan project and is not affiliated with or endorsed
> by Terry Pratchett's estate or publishers.

## Status

Working today: import of the Audible Library Extractor (ALE) CSV export, normalization,
SQLite storage, exact library queries, a FastAPI service, and **natural-language questions**
answered by a local Ollama model (phase 2, below). Recommendations are next.

## Design principles

- **Local first, free only.** Runs on your machine; the language model is served locally by
  [Ollama](https://ollama.com). No paid services or API keys.
- **Don't make an LLM do exact database work.** Counts, filters and series status are plain
  code over SQLite (`library/queries.py`). The model is reserved for fuzzy natural-language
  interpretation and for explaining results.
- **Answers must be grounded.** The model's wording is checked against the rows that were
  actually returned; if it mentions a title, name or number that isn't in them, it is
  discarded and replaced with plain text. Questions the data can't answer get an honest
  "I can't answer that" instead of a guess.
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

## Asking questions

Needs the Ollama model (`make up` and `make pull-model`, or a local Ollama on port 11434).

```bash
l-space ask "Which narrators do I listen to the most?"
l-space ask "Which books run longer than 15 hours?" --show-plan
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "Do I have gaps in any of my series?"}'
```

Inside Docker, run the same commands with `docker compose exec app l-space ...`; the
`./data` folder is mounted, so an import is shared with the host.

How a question is answered:

1. **Route.** The model turns the question into a `QueryPlan` (`ask/plan.py`): one operation
   (`list_books`, `count_books`, `narrator_counts`, `author_counts`, `series_gaps`,
   `in_progress`, or `unsupported`) plus optional filters (author, narrator, series, category,
   length, your rating) and a sort. Ollama constrains decoding to that JSON schema, and an
   invalid plan is retried once before the question is declined. A plan that picks a real
   operation but also explains why it can't answer is treated as `unsupported`.
2. **Execute.** Plain code runs the plan over the library, so counts and filters are exact.
3. **Phrase.** The model writes a short answer from the returned rows only. If nothing
   matched, or the question was unsupported, no model-written text is used at all.
4. **Check.** `ask/grounding.py` rejects wording that mentions a name, title, or number that
   isn't in the rows (or the question) and falls back to deterministic text. The check is a
   conservative heuristic, not a proof.

Routing is a short call where a mistake ruins the answer, while writing is the slow one, so
they can use different models: set `L_SPACE_OLLAMA_ROUTER_MODEL` (for example `qwen2.5:7b`)
to route with a larger model and keep a small one writing. Both stay loaded in Ollama.

`--show-plan` prints the plan, whether the draft passed the check, and timings. Recommendation
and "what happens in this book" questions are deliberately `unsupported` for now.

## Evaluation

`evals/` holds two fixed question sets, run against the synthetic sample library so anyone can
reproduce them:

- `questions.jsonl`: 20 questions (15 answerable, 5 not) used while tuning the prompt.
- `heldout.jsonl`: 16 differently-worded questions (12 answerable, 4 not) that are **never
  used for tuning**. Quote this score; the tuning set is optimistic by construction.

```bash
make eval-validate   # no model needed: checks both datasets
make eval            # tuning set through the configured Ollama model
python evals/run.py --questions evals/heldout.jsonl          # held-out set
python evals/run.py --model qwen2.5:7b --out evals/results.json   # one model for both stages
python evals/run.py --router-model qwen2.5:7b                     # bigger router, small writer
```

It reports plan match, result match (does the model's plan return the same books as the
expected one, even if phrased differently), abstention on unanswerable questions, false
abstentions, how often the model's wording passed the grounding check, how often fallback text
was used, and p50/p95 latency with a route/write split. See `evals/README.md` for the results
log.

## Layout

```text
src/l_space/
  api/         FastAPI routes
  ask/         question answering: plan schema, router, executor, grounding check
  core/        settings
  ingestion/   ALE CSV loading and normalization
  library/     models, SQLite store, deterministic queries
  models/      the only module that talks to a language model
tests/         unit tests; the model is replaced by a scripted fake
evals/         fixed questions and scoring, kept outside the app code
data/sample/   synthetic demo library in the ALE schema
```

Folders such as `recommend/` or `workflows/` will be added when those features are real.

## Roadmap

1. Phase 1: import, normalize, exact queries, API (done)
2. Phase 2: natural-language questions routed to exact queries, grounded answers, evals (done)
3. Phase 3: semantic search over tags and blurbs with local embeddings, then explainable
   recommendations with correctable preferences and hand-judged evals
