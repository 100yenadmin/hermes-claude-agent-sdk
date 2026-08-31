# Contributing

Development happens against the exact host candidate named in
`docs/compatibility.md`. Keep Claude-specific policy and dependencies in this
repository and provider-neutral host policy upstream.

Before submitting a change:

1. Use Python 3.11, 3.12, or 3.13.
2. Install `.[test]` into an isolated environment.
3. Run `python -m pytest` and `python -m build`.
4. Confirm no credentials, auth files, cookies, raw configuration, prompts,
   transcripts, session identifiers, customer data, or private endpoints enter
   source, fixtures, logs, or artifacts.

Changes derived from PR #65982 must preserve verified author, committer, date,
commit-message, and trailer provenance where practical. Structural extraction
commits must cite their original source SHAs without falsely reassigning work.
