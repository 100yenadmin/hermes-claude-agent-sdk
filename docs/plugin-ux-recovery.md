# Fable plugin UX recovery — unreleased

This phase improves the existing standalone plugin for one isolated Hermes
installation. It is not a new provider or a DirectSDK comparison. Follow
[tracker #1](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1) and
[acceptance #27](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/27)
for current status.

## Review route and current proof

- [Plugin correction PR #30](https://github.com/100yenadmin/hermes-claude-agent-sdk/pull/30)
  stacks on #29. Review visible-message acknowledgment, continuity and usage.
- [Generic host correction PR #22](https://github.com/100yenadmin/hermes-agent-for-upstream-PR-only/pull/22)
  adds durable message acknowledgment, nullable request accounting and
  generation settings. It is in our fork; no upstream merge is implied.
- The installed thin gate passed on plugin source
  `d4f01a602a0c5ffc848e582a0231f293c2aea468`, host
  `3302d330ff10cd26a80991b1db44c22ea270711f`, wheel SHA-256
  `a6c9cf19c59c52c24dd349bff3540e88936c5816ed6b248c41917de32c882fa3`.
  [Public checkpoint](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/27#issuecomment-5651093608).
  The wheel was built from `bde73e1`; the subsequent source change only pinned
  CI to the corrected host. This is not a whole-wheel equality claim.
- That normal, non-hidden Hermes Gateway session proved tools, approval denial
  and recovery, saved commentary before tools, restart with the same native
  identity, subscription-included billing and zero processes left after teardown.
- Focused independent implementation review passed, including the cold-resume
  correction. The broader current CI and remaining UX checklist are not yet
  complete. Six sealed historical-v4 tests require their original host and are
  not counted as current-candidate passes.

The native dependency remains SDK `0.2.151`, bundled CLI `2.1.258`, model
`claude-fable-5-1`. No lower-usage or universal-model claim is made.

## What changes

Hermes acknowledges each assistant message durably before related tool execution.
Separate message identities preserve repeated text and reject conflicting
snapshots. Long text is chunked rather than clipped. Graceful cancellation
retains observed commentary and marks continuity interrupted.

One terminal usage receipt represents each execution attempt. Host steps,
SDK-reported native turns and observed requests are separate. An unknown request
count is shown as unknown, not zero or one; SDK turns are not HTTP requests.

Native continuation requires an unchanged prior-history prefix, compatible
prompt/tool context and valid provenance. A normal appended user message is not
an edit. Where a trustworthy native edit/branch boundary is unavailable, Hermes
history is preserved and the plugin starts a separate native session with an
explicit **CONTEXT HANDOFF**: a bounded historical excerpt with omission notices,
not a semantic summary or exact native replay. Current instructions are labeled
separately. Hidden compacted state cannot be reconstructed.

Hermes effort/thinking selections pass through validated SDK options. Unsupported
values fail explicitly. Hermes retains tools, policy, approvals, memory, skills
and delegation. Native Claude tools, settings and the Claude Code prompt preset
remain disabled.

## Isolated installation and removal

Do not install over a shared/default profile or replace the public release.
Use a separate checkout/virtual environment and a consenting isolated profile.
Before installation, snapshot that profile's package/config/state; preserve
authentication and conversations during rollback.

1. Check out host `3302d330ff10cd26a80991b1db44c22ea270711f` from the linked fork.
   Install its existing dependencies in a separate Python 3.11 environment.
2. Download the wheel from an exact-head successful artifact build linked in
   PR #30. Verify its digest against the corresponding candidate receipt.
   Do not substitute the old v0.1.0 release wheel. Artifacts are temporary CI
   candidates, not a new publication.
3. In that environment, install the wheel with `python -m pip install /path/to/wheel.whl`.
   Run `hermes-claude-agent-sdk doctor --json`; all required host capabilities must pass.
4. Use normal Claude CLI subscription login. Never provide an API key or enable
   Extra Usage as a fallback. Select the isolated profile in Hermes, enable the
   `claude-agent-sdk` plugin, and select provider `claude-agent-sdk`, model
   `claude-fable-5-1`, runtime mode `agent_runtime`.
5. Create a normal non-hidden session; verify saved commentary, harmless tools,
   denial/recovery and resume. Follow the remaining acceptance status in #27.

See the [existing explicit profile/model/vision configuration steps](maintainer-guide.md#try-the-pinned-host-release-in-isolation),
but use the **new host and candidate wheel above**, not that historical release's pins.
For removal, stop this isolated Gateway normally, disable the plugin in only
that profile, uninstall with `python -m pip uninstall hermes-claude-agent-sdk`,
and restore the recorded prior package/config if needed. Do not delete native
authentication or conversation state. Verify no isolated worker survives.

## Known differences — not waived by UX proof

The SDK still runs a Claude Code-derived subprocess and owns internal generation
scheduling and compaction. Per-generation Hermes hooks/budgets are not established;
[admission #21](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/21)
and [ordinary-loop #22](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/22)
remain open. Context handoff has reduced fidelity. Abrupt crashes or uncertain
tool completion must not trigger automatic side-effect replay. Technical billing
evidence is not service-terms certification. No merge, publication, stock-Hermes
compatibility or customer-readiness claim is made.
