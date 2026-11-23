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
| 2026-11-21 | Prompt rules and examples, reason guard, row-level scoring (`6cd90a6`) | questions | 0.923 | 0.923 | 1.00 | 1.00 | 14.5 s / 54.5 s |
| 2026-11-21 | Same (`6cd90a6`) | held-out (14) | 0.800 | 0.800 | 1.00 | 1.00 | 18.1 s / 53.3 s |
| 2026-11-21 | Lower/upper-bound and which-narrators rules (`257ecf2`) | questions (20) | 0.800 | 0.800 | 1.00 | 0.929 \*\* | 14.9 s / 61.4 s |
| 2026-11-21 | Same (`257ecf2`) | held-out (16) | 0.833 | 0.833 | 1.00 | 1.00 | 14.6 s / 38.2 s |
| 2026-11-21 | Router `qwen2.5:7b`, writer `llama3.2` (`c22f396`) | questions (20) | 1.00 | 1.00 | 1.00 | 1.00 | 15.1 s / 127.8 s † |
| 2026-11-21 | Same (`c22f396`) | held-out (16) | 1.00 | 1.00 | 0.75 | 1.00 | 15.7 s / 125.8 s † |

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

Second row, in detail: 12 of 13 answerable questions routed correctly, no false abstentions,
no router errors, all 12 model-written answers passed the grounding check (no fallbacks). The
one miss is "Which narrators have I rated five stars?", where the model chose `list_books`
with a rating filter instead of `narrator_counts`. Latency split (p50): routing 3.6 s, writing
13.4 s, so the writing call is the expensive one.

This row is on the tuning set, which the prompt was improved against, so treat it as an upper
bound. The held-out row is the one to quote.

### Held-out run, and what it led to

The first held-out run (14 questions) scored 0.800 for plan and result match, against 0.923 on the
tuning set. That gap is the expected cost of tuning, and with 10 answerable questions each miss
is 10 points, so the exact figure is noisy. Abstention (1.00) and grounding (1.00) held up on
unseen questions. Latency split (p50): routing 3.9 s, writing 18.6 s on an older CPU.

Both misses were patterns, not one-offs:

- "go **beyond** 16 hours" became `max_minutes`. The prompt gave a keyword list and the model
  followed it literally. The rule now describes `min_minutes` and `max_minutes` as a lower and
  an upper bound, with the keywords as examples.
- "Which **narrators** ... rated at least four stars" became `list_books`, the same failure as
  `narrators-five-star` on the tuning set. Two independent questions failing the same way
  made it systematic. The prompt now says a "which narrators / authors / who" question is
  `narrator_counts` or `author_counts` even when it also filters, and has an example.

Those two held-out questions were then used for tuning, so they moved into `questions.jsonl`
(now 20) and four newly worded ones joined `heldout.jsonl` (now 16), testing the same
patterns with different phrasing ("17 hours or more", "no more than 14 hours", "the narrators
behind my four-star-and-up books", plus a count with a length filter). Scores from before and
after this change are therefore not directly comparable on the held-out set. The next rows should
rerun both sets.

### The rules did not work on a 3B model

The two sets changed between the rows above (questions moved in, new ones added), so compare
by question, not by score. The twelve tuning questions that passed before still pass, so
nothing regressed, but **neither rule fixed its target**:

- "beyond 16 hours" is still `max_minutes: 960`, and the new held-out "17 hours or more"
  fails the same way. Common phrasings ("longer than", "no more than") pass; unusual ones do not.
- "Which narrators ..." questions still route wrongly, three different ways: `list_books`,
  `author_counts`, and `list_books` with an invented `sort: longest`, which ignores the
  "never invent filters" rule.

Prompt patching has reached diminishing returns on this model, and each round also spends
held-out questions. \*\* The grounding dip (0.929) was a bug in the check, not the model: the
question said "five stars" and the answer said "5 stars". Digits now match number words in the
question.

**Next experiment: is it model capacity or the interface?** Routing is only about a quarter of
the time per question (p50 4 s of ~15 s), so a larger model can do it without making the
slow writing step slower. Pull a bigger model and route with it, keeping `llama3.2` as the writer:

```bash
docker compose exec ollama ollama pull qwen2.5:7b
docker compose exec app python evals/run.py --router-model qwen2.5:7b
docker compose exec app python evals/run.py --router-model qwen2.5:7b --questions evals/heldout.jsonl
```

If the larger router fixes these patterns, the answer is "right-size the model per stage"
and no more prompt tuning is needed. If it does not, the interface is the problem, and the
candidates are naming fields after the user's words (`longer_than_minutes`, `rank_narrators`)
and a short reasoning field before the operation. The held-out questions that failed in the
latest run are now burned for that purpose, so fresh ones would be needed.

† Every p95 in this log is really the slowest single question. The old calculation picked the
last sorted value for any run of 13 to 20 questions, which is the maximum. For these two rows
that maximum (about 126 s) is probably the 7B model loading from disk on the first question.
The harness now loads the models before timing starts and reports a nearest-rank p95 and the
max separately, so future rows are comparable.

### Result: it was model capacity

With the prompt unchanged and only the routing model swapped (`--router-model qwen2.5:7b`, the
3B `llama3.2` still writing):

| | 3B router | 7B router |
|---|---|---|
| tuning set plan / result match | 0.800 | **1.000** |
| held-out plan / result match (12 answerable) | 0.833 | **1.000** |
| route p50 | ~4 s | ~8 s |
| total p50 | ~15 s | ~15 s |

Routing is a short call and writing dominates the time, so a better router is cheap: about 4 s
more routing for a result that went from roughly one wrong plan in five to none. It also needs
about 5 GB more RAM with both models loaded.

The held-out set is still uncontaminated by prompt tuning: it was not used to change the prompt
after the failing run, only the model changed. The caveats are real, though. There are only
12 answerable held-out questions, the sample library has 12 books, and none of this has been
tried on the real library with natural phrasing.

**The one miss is a known gap, not a routing bug.** "What's a good audiobook for a long road
trip?" is a recommendation question, which is `unsupported` for now, but the 7B router read it
as "long books" (`list_books`, `min_minutes: 600`, `sort: longest`). The result is true to the
library but not an honest answer to "good". It is deliberately left untuned: recommendations
are the next phase, and when a `recommend` operation exists this question should move from the
unanswerable group to the answerable one.
