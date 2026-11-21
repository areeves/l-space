# Evals

Fixed questions and scoring code live here, deliberately outside `src/`, so changes to
prompts, the plan schema, retrieval, or the model can always be compared against the same set.

- `questions.jsonl`: one question per line with the plan we expect (`expect`) and, for
  answerable ones, how many matches that plan must produce on the synthetic sample library
  (`expect_total`, optionally `expect_first_title`). Unanswerable questions expect `unsupported`.
- `run.py`: runs the questions through the real pipeline and reports the metrics defined in
  its docstring. `--validate` checks the dataset itself without a model.

Because the sample library is synthetic and committed, results are reproducible by anyone.
For your own library, write a private question file (for example in `data/private/`) and pass
`--questions` and `--library`.

Next, for the recommendation phase: hand-judged "would I actually listen to this next?" cases,
plus checks that an explanation only cites signals (narrator, series, length, category) that
really exist on the books involved.
