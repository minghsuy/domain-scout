1. **Analyze the problem:** `_strategy_seed_expansion` in `domain_scout/scout.py` is too long because it contains a deeply nested loop that processes CT records for seed expansion.
2. **Design the improvement:** Create a helper method `_process_seed_expansion_record` (similar to the existing `_process_org_record` helper) to process individual CT records and return the list of evidence.
3. **Refactor:** Move the inner logic of the `for rec in records:` loop inside `_strategy_seed_expansion` to the new `_process_seed_expansion_record` helper function.
4. **Test:** Run formatters (`uv run ruff format .`), linters (`uv run ruff check --fix .`), and the test suite (`uv run --all-extras pytest`) to ensure the behavior is unchanged.
5. **Complete pre-commit steps:** Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.
6. **Submit:** Submit the Code Health PR using `submit`.
