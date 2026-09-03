# Contributor and source provenance

This document is the sanitized provenance record for the standalone package.
It separates immutable upstream references from the standalone package's own
commits and from later host/API adaptation. The v3 extraction and parity
identities below are historical only. This is not runtime, merge, release, or
customer proof.

## Identities

| Role | Immutable identity | Meaning |
| --- | --- | --- |
| Standalone bootstrap | [`79a5932f8ef2a7ce3428db0c11a285369b7ed42d`](https://github.com/100yenadmin/hermes-claude-agent-sdk/commit/79a5932f8ef2a7ce3428db0c11a285369b7ed42d) | Package and policy shell used as the documentation base; it intentionally has no working SDK runtime. |
| Upstream source remote | [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) | Public source repository from which the extraction boundary was defined. |
| Upstream PR | [PR #65982](https://github.com/NousResearch/hermes-agent/pull/65982) | Claude Agent SDK provider and related host changes; the complete PR is not claimed as standalone package content. |
| Upstream PR tip | [`41def1e24e5223efa246d9fc57575db7181c6021`](https://github.com/NousResearch/hermes-agent/commit/41def1e24e5223efa246d9fc57575db7181c6021) | Exact inspected source tip. |
| PR range base | `4f22543509d1b91dc45bcb369447126c5eb14fb7` | Merge-base used to describe the complete PR range, not a package version. |
| Source snapshot | `3c1321f16744747550384a1b96fa4529ba23ffe1` | Starting point for the six current-head fixes listed in the extraction evidence. |
| Frozen parity baseline (v3 historical) | `6967371b9ff8efce9372dd428b3b764322bd6481` | Historical downstream parity candidate; not the PR tip and not the source of this package history. |

The standalone bootstrap SHA and the upstream source SHA must remain separate in
release notes, manifests, and reviews. A future filtered or adapted commit has
new Git identities when its tree, parents, or metadata change; the original
source SHA remains an immutable provenance reference only.

## Revision 4 candidate identity

Plugin commit `0af5e6481a50adf551f3eaa6055dac88e6a670db` is the implementation
checkpoint immediately before this documentation revision; it is not the final
self-referential plugin identity. Candidate proof binds the final exact plugin
commit and wheel digest in the v4 result manifest, paired with Hermes host
commit `c5921dd61daa1365dab55d35286316df44d44759`. The compatibility target is SDK
`0.2.151`, bundled Claude Code-derived CLI `2.1.258`, and direct model
`claude-fable-5-1`. These source identities describe the Hermes-owned,
zero-native Revision 4 boundary; they do not replace the historical v3
provenance and do not prove an upstream merge, publication, future
compatibility, or customer readiness.

## Verified human attribution

The following public identifiers were verified in the source history or in an
explicit attribution body. The source history contains other generic author
display names; those are not silently converted into human identities.

| Public identifier | Evidence in the source history | Attribution boundary |
| --- | --- | --- |
| `fcavalcantirj` | Initial provider landing [`b7ee339b`](https://github.com/NousResearch/hermes-agent/commit/b7ee339b3408912000c8d3b6af27d9bfedcd69c1), PR merge/follow-up commits, and PR tip [`41def1e2`](https://github.com/NousResearch/hermes-agent/commit/41def1e24e5223efa246d9fc57575db7181c6021). | Source author for the cited commits; not an assertion that this contributor authored all extracted or adapted code. |
| `CryptoKylan` | Runtime, transport, safety, context, test, and current-head fixes, including [`95ee0e67`](https://github.com/NousResearch/hermes-agent/commit/95ee0e671d439fdbb0deec64d6422f705d94f854), [`4ebf7e5c`](https://github.com/NousResearch/hermes-agent/commit/4ebf7e5c2f40ffc433256bf29b7a0bfc91a2da7c), and [`34a1bbf5`](https://github.com/NousResearch/hermes-agent/commit/34a1bbf569549ad4cddbdba782f681bf2433e087). | Source author for the cited commits only. |
| `PyroFilmsFX` | SDK startup, stream, and gateway follow-ups, including [`8f249a0e`](https://github.com/NousResearch/hermes-agent/commit/8f249a0e7f5a447f8987cd9060ca8000783268e0), [`bc751fb4`](https://github.com/NousResearch/hermes-agent/commit/bc751fb457c12bac11505e68978a0c85dc45197f), and [`87bff093`](https://github.com/NousResearch/hermes-agent/commit/87bff093cd7483953ccdde01fda8ba510ad158f5). | Source author for the cited commits only. |
| `Romain` / `romain-bury` | Source author display name `Romain` on bridge commits such as [`8380da12`](https://github.com/NousResearch/hermes-agent/commit/8380da12df9650674cf23ec04dcbc7bc6813570d); merge metadata identifies the bridge follow-up as `romain-bury`. | The two identifiers are recorded as source metadata; this document does not infer any additional legal identity. |

The source body of [`8380da12`](https://github.com/NousResearch/hermes-agent/commit/8380da12df9650674cf23ec04dcbc7bc6813570d)
also explicitly credits Akshay CM (`akshaynexus/hermes-agent`) for the shared
`hermes_tool_exposure.py` layer and in-process bridge design, based on
[PR #56413](https://github.com/NousResearch/hermes-agent/pull/56413). That is
a focused third-party bridge attribution, not a claim that the contributor
authored all Claude SDK or standalone package code.

## Source versus adaptation

The extraction evidence defines a plugin-owned allowlist and excludes
host-generic routing, persistence, approval, gateway, and CLI internals. Any
translation to AgentRuntime v1 public contracts is a new standalone/API
adaptation commit. It must not be presented as a surviving upstream source
commit or as proof that the upstream PR was merged.

The current standalone bootstrap is intentionally non-functional. In
particular, this provenance record does not prove SDK import, credential lookup,
subprocess start, model execution, subscription billing, tool execution,
installation, release, or customer readiness.

## Sanitization rules

- Contributor names, public handles, dates, SHAs, subjects, and public PR links
  may be retained.
- Contributor email mappings, credentials, cookies, environment values, raw
  transcripts, customer identifiers, and session material are excluded.
- Codex and the extraction/documentation lane are not substituted for upstream
  human authors.
- Source history, package history, host/API adaptation, and runtime proof are
  separate claims and require separate evidence.
