# Evals

Fixed questions and scoring code live here, deliberately outside `src/`, so changes to
prompts, the plan schema, retrieval, or the model can always be compared against the same set.

- `questions.jsonl`: one question per line with the plan we expect (`expect`) and, for
  answerable ones, how many matches that plan must produce on the synthetic sample library
  (`expect_total`, optionally `expect_first_title`). Unanswerable questions expect `unsupported`.
- `run.py`: runs the questions through the real pipeline and reports the metrics defined in
  its docstring. `--validate` checks the dataset itself without a model.

- `heldout.jsonl`: differently-worded questions that are never used to tune prompts or code.
  Look at the failures in `questions.jsonl` to improve the system, but report the `heldout`
  score, since anything you tune against will score better than it generalizes.

Because the sample library is synthetic and committed, results are reproducible by anyone.
For your own library, write a private question file (for example in `data/private/`) and pass
`--questions` and `--library`.

Next, for the recommendation phase: hand-judged "would I actually listen to this next?" cases,
plus checks that an explanation only cites signals (narrator, series, length, category) that
really exist on the books involved.

## Results log

Same sample library, `llama3.2` (3B) on CPU in Docker, temperature 0. Add a row after each
change so the effect of each fix is visible.

| Date | Change | Set | plan match | result match | abstain recall | grounded | p50 / p95 latency |
|---|---|---|---|---|---|---|---|
| 2026-11-20 | Baseline (PR #2) | questions | 0.692 | 0.769 \* | 0.80 | 0.917 | 17.9 s / 58.3 s |

\* Scored by totals only, which let one wrong plan pass by coincidence (3 five-star books vs
3 five-star narrators). The current scoring compares the returned rows, which by my reading
puts this baseline at 0.692. The one grounding fallback was a bug (`5.0` vs `5`), now fixed.

What the baseline failures looked like, and what changed in response:

- "shorter than an hour" became `min_minutes: 60`: added explicit min/max wording rules and
  an example.
- "narrated by" put the narrator in `author`: added author-versus-narrator rules and an example.
- "in the middle of" did not map to `in_progress`: added an example and a list of what the
  library records.
- "wishlist": the model wrote the right reason but chose `list_books` anyway: such a plan is
  now treated as `unsupported`, and the prompt lists what the library does *not* record.
- Latency: routing and writing are now timed separately, and both calls have a token cap.

The next row should come from rerunning both sets after these changes.
