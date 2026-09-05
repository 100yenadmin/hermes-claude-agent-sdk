# Hermes release acceptance and preserved Revision 4 benchmark

The current release gate is [Hermes release acceptance](hermes-release-acceptance.md).
The owner-directed 2026-09-05 correction removes the blanket native-36 / 220-path /
390-packet release obligation, not supported Hermes behavior or safety proof.
The following sections describe the preserved v4 benchmark mechanics. Its catalog,
graders, fixed-count release validator and historical results remain unchanged;
their aggregate `PENDING` is not rewritten as a benchmark pass. Use #9's reviewed
Hermes acceptance readback for the corrected release decision.

`parity-contract-v4.yaml` is the preserved source and behavior catalog for the
Hermes-owned Claude subscription runtime. It is pinned to SDK `0.2.151`, the
bundled Claude Code-derived CLI `2.1.258`, and direct model
`claude-fable-5-1`. The v3 contract and evidence packet remain immutable
historical predecessors; v3 is not a current support, merge, or release claim.

## Gate separation

- `rc` is the deduplicated union of the preserved v3 source rows, 23 SDK
  boundary invariants, and the named historical behavior packs. Revision 4
  adds zero-native, exact-prompt/settings/MCP, canonical transcript/stream,
  Hermes `delegate_task`, and host-background ownership preflights.
- `runtime` contains the bounded 100-turn same-session campaign. Longer
  observation belongs here only when a row names a genuinely clock-dependent
  invariant and explains why simulated time or an active restart is
  insufficient.

The source row remains visible even when several rows share an `execution_id`.
Sharing execution is deduplication; it never turns an exclusion into a pass.

The v4 contract binds its executable catalog to the immutable predecessor
inputs named in `contract.predecessor` and to the current plugin/host candidate
identities. The v3 source packs and ledgers are historical accounting only;
they are not current execution or pass authority. The v4 contract/runner path
verifies the bound hashes and full source-to-catalog bijection before accepting
evidence.
The historical parity modules, executors, and console-script definitions are
source/sdist-only QA interfaces; none is installed by the runtime wheel. The
v3 runtime suite is also legacy-only. The v4 contract/runner modules are the
current source-level evidence boundary.

## Dynamic tool inventory

The inventory input is a sanitized YAML or JSON mapping:

```yaml
schema_version: 1
profile_id: hermes-v4-isolated
profile_hash: 3333333333333333333333333333333333333333333333333333333333333333
declared_tools:
  - name: repo_read
    input_schema:
      type: object
      properties:
        path: {type: string}
      required: [path]
observed_tools:
  - name: repo_read
    input_schema:
      type: object
      properties:
        path: {type: string}
      required: [path]
```

`--profile-manifest` points to a separate sanitized JSON object containing
`schema_version`, `profile_id`, `isolation_kind` (`in_process_fixture` or
`local_profile`), `persistent`, `shared_state: false`, `customer_data: false`,
and a `configuration_hash`. The inventory's `profile_hash` must equal the
canonical hash of that manifest; a caller-supplied unmatched digest fails
closed. The candidate configuration must also retain the direct Hermes prompt,
`tools=[]`, `setting_sources=[]`, one strict `hermes-tools` MCP server, and only
exact `mcp__hermes-tools__<tool>` names from the delivered request.

Names and canonical schema hashes must match exactly. A missing, unknown,
duplicate, extra, or changed tool/server is a contract violation (exit `2`).
The inventory describes the host-delivered request only; it is not permission
to discover or enable Claude-native tools. The result packet retains only the
inventory hash, never raw prompts, sessions, auth material, credentials, or
customer identifiers.

## Historical executors and v4 evidence

The historical v3 executors were explicitly registered through the
`hermes_claude_agent_sdk.parity_executors` Python entry-point group. The entry
point name is an `execution_id`, or its loaded object may be a mapping of exact
execution IDs to callables. There is no default or fuzzy fallback. An unknown
execution ID writes a sanitized `PENDING` packet and returns exit `75`.

Those historical executors recorded a hash-bound `run-manifest.json` beside
result packets.
That manifest preserves every executed receipt and cumulative turn count across
resume. The RC lane is hard-capped at 180 synthetic turns; the runtime lane is
hard-capped at one 100-turn campaign. A combined executor may return all three
path outcomes from one scenario execution, so positive, denial, and recovery
evidence does not imply three redundant campaigns.

Path applicability is explicit in the catalog. Historical behavior-pack rows
and the runtime campaign require independent positive, denial, and recovery
outcomes. Each predecessor row and SDK boundary row is instead one mandatory
source invariant: its positive invariant proof is required, while synthetic
secondary paths are marked `required: false` with a rationale. The grader
reports those paths as `NOT_REQUIRED`; they never count as passes or source
exclusions.

The historical active behavior executor used isolated subscription turns for
source/docs,
image input, memory recall and thread isolation, restart/tool continuity, and
repository-instruction followthrough. Delegation is exercised only through the
Hermes `delegate_task` tool; detached completion is checked on the host's
background rail. There is no supported Claude-native Agent or background route.
Model-switch fencing, native compaction/exact-once mutation, and stale-result
settlement use exact-source deterministic tests when forcing those boundaries
against the operator subscription would be unsafe or nondeterministic. The
approval behavior remains the installed-plugin thin gate through the real host
tool bridge.

Every historical executable parity candidate was bound to the installed
`claude-agent-sdk` distribution before host imports, subprocesses, or live
work. Revision 4 requires SDK `0.2.151`, bundled CLI `2.1.258`, and direct
model `claude-fable-5-1`; missing, malformed, or different identity fails
closed. The v3 source ledger remains unchanged as historical evidence.

The v4 map and evidence grader bind focused evidence to the exact plugin SHA
and wheel digest supplied by the completed candidate receipt, host SHA
`15039e4f2d096b06f56369fbd78be09f3be73065`, and its immutable predecessor
inputs. Zero or missing candidate identities fail closed. Historical
source-pack graders run in an isolated subprocess against synthetic host tools.
Neither adapter calls Telegram, shared Eva, customer state, a browser, a
scheduler, or an external messaging surface.

The pinned historical source-pack grader runs first for those predecessor rows.
Two source items retain an explicit Hermes behavior-code overlay because their
checks require exact English strings or one JSON field name even though the
source prompt defines a behavior. The overlay preserves source grade hashes,
process scores, efficiency, and safety results; it cannot rescue copied
distractors, a wrong agent, missing action, unsafe result, or source safety
failure. It does not introduce a Claude-native Agent route. Effective sandbox
cron defaults are normalized into the trace so an omitted `recurring` argument
records the one-shot `false` behavior executed by Hermes.

Each result binds the v4 contract/catalog hashes, exact plugin and host SHAs,
SDK/CLI identity, sanitized profile hash, runner version, and tool inventory
hash. Passing packets require exactly one terminal event plus both primary and
secondary proof hashes. Consequential, initially failing, or unstable paths
require three consecutive passes with one unchanged candidate identity. The
grader reports `pass@3` and strict `pass^3` separately.
For every successful positive or recovery packet, normalized event kinds must
exactly match the catalog's `expected_trace`; a proof hash attached to the wrong
trace grades as a verified failure. Expected denials must end in one explicit
denied terminal.

## Runtime evidence receipt

The v4 evidence grader accepts no branch name, editable-install identity, or
unverified artifact. Any local evidence receipt must identify the exact plugin
and host SHAs, SDK/CLI identity, contract and catalog hashes, and immutable
artifact digest. It contains no credentials, prompts, sessions, or customer
data. A receipt is evidence for this bounded candidate, not a release-ready or
customer-ready claim.

The historical campaign definition performs 100 main-session turns, injects and recovers from a host
tool denial, checks memory and cross-runtime state fencing, uses one image,
executes exact-once synthetic write and cron mutations through Hermes, exercises
the Hermes `delegate_task`/background boundary, closes and resumes at turn 50,
and verifies process teardown. It does not start a Claude-native Agent or
background route. Only the 100 main-session turns count against the runtime
budget; fail-closed state and teardown probes do not reach the provider.

The portable `result-packet-v3.schema.json` shape is retained as historical
interchange material. Revision 4 Python validation is intentionally stricter:
it recalculates trace, candidate, and packet hashes;
rejects unsafe billing, invariant violations, silent fallbacks, forbidden raw
fields, and duplicate trials; and never treats `PARTIAL` as a gate result.

## Proof boundary

Source inventory or deterministic tests do not prove a live subscription turn.
Any live evidence is bounded to one exact plugin/host artifact pair and the
isolated profile used by the campaign. Nothing here authorizes or proves merge,
publication, future Hermes/SDK compatibility, shared Eva, fleet operation, or
customer readiness.
