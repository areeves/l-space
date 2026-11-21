"""Natural-language questions over the library.

The model never touches the data. It (1) turns a question into a validated ``QueryPlan``,
and (2) phrases the rows that plain code returned. A grounding check rejects phrasing that
mentions anything not present in those rows.
"""
