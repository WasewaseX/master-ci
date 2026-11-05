# Fix engine report - applied fixes

| tool | tier | status | detail |
| --- | --- | --- | --- |
| ruff-format | safe | fixed | 9 files left unchanged |
| markdownlint | safe | fixed | Summary: 0 issues in 0 files |
| ruff-fix | lint | fixed | All checks passed! |
| semgrep | lint | fixed |           305┆ urllib.request.urlretrieve(SHFMT_URL, binary) |
| generic | safe | fixed | 0 of 41 files normalized |

Never auto-fixed (tracked in the audit issue instead, they need a human): secrets (rotate them), dependency vulnerabilities (upgrade decisions), AI-integrity prose findings (rewriting meaning is not mechanical).
