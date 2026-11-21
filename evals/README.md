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
