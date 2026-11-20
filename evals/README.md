# Evals

Held-out questions and scoring code live here, deliberately outside `src/`, so changes to
prompts, retrieval, or recommendation logic can be compared against the same fixed set.

Planned for the recommendation phase: a small set of hand-judged "would I actually listen to
this next?" cases built from the synthetic sample library, plus checks that an
explanation only cites signals (narrator, series, length, rating) that really exist on the
books involved. Results get reported in the main README.
