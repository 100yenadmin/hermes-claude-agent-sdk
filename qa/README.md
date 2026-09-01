# Hermes Agent SDK parity v3

`parity-contract-v3.yaml` is the versioned source and behavior contract for
Hermes Agent SDK Feature Parity `v3.0.0`. It succeeds the frozen v2 RC gate; it
does not edit or reinterpret the v2 evidence packet.

## Gate separation

- `rc` is the deduplicated union of all 53 v2 non-soak cells, 12 OpenClaw
  active behaviors, 23 Agent SDK boundary invariants, and 36 ClawProBench
  native scenarios.
- `runtime` contains the active 100-turn same-session campaign. Longer
  observation belongs here only when a row names a genuinely clock-dependent
  invariant and explains why simulated time or an active restart is
  insufficient.

The source row remains visible even when several rows share an `execution_id`.
Sharing execution is deduplication; it never turns an exclusion into a pass.
For the five boundary-ledger rows classified `not_runtime_applicable`, the
upstream positive path is explicitly non-required and emits no pass packet.
Their fail-closed denial and supported-path recovery adaptations remain
mandatory, so source accounting stays complete without claiming an absent SDK
surface.

`contract.source_authority` binds the executable catalog to four immutable
repo inputs. The boundary authority is `agent-sdk-boundary-ledger-v3.yaml`,
which covers the runtime, permissions, and structured-input suites. The older
`parity/v3/source-packs/sdk-boundary.json` and `parity/v3/sdk-ledger.json` are
preliminary runtime-test-only accounting artifacts; they are explicitly
excluded from execution and pass authority. Every CLI command verifies the
bound file hashes and the full source-to-catalog bijection before proceeding.

## Dynamic tool inventory

The inventory input is a sanitized YAML or JSON mapping:

```yaml
schema_version: 1
profile_id: fable-v3-isolated
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
closed.

Names and canonical schema hashes must match exactly. A missing, unknown,
duplicate, or changed tool is a contract violation (exit `2`). The result
packet retains only the inventory hash, never raw prompts, sessions, auth
material, credentials, or customer identifiers.

## Executors and evidence

Executors are explicitly registered through the
`hermes_claude_agent_sdk.parity_executors` Python entry-point group. The entry
point name is an `execution_id`, or its loaded object may be a mapping of exact
execution IDs to callables. There is no default or fuzzy fallback. An unknown
execution ID writes a sanitized `PENDING` packet and returns exit `75`.

The runner records a hash-bound `run-manifest.json` beside result packets.
That manifest preserves every executed receipt and cumulative turn count across
resume. The RC lane is hard-capped at 180 synthetic turns; the runtime lane is
hard-capped at one 100-turn campaign. A combined executor may return all three
path outcomes from one scenario execution, so positive, denial, and recovery
evidence does not imply three redundant campaigns.

The active-12 executor uses live, isolated subscription turns for source/docs,
image input, native Agent handoff/fanout, memory recall and thread isolation,
restart/tool continuity, and repository-instruction followthrough. Model-switch
fencing, native compaction/exact-once mutation, and stale-background settlement
use exact-source deterministic integration tests because forcing those failure
boundaries against the operator subscription would be unsafe or nondeterministic.
The approval behavior remains the installed-plugin thin gate through the real
host tool bridge.

The frozen-v2 map runs focused evidence at immutable v2 SHA `33fe73a`, the exact
current plugin SHA, and the exact provider-neutral host SHA. The native-36 map
runs each pinned ClawProBench grader in an isolated subprocess against a real
Claude SDK turn and synthetic host tools. Neither adapter calls Telegram,
shared Eva, customer state, a browser, a scheduler, or an external messaging
surface.

`v2:ops-08` validates `dependency-restore-manifest-v3.txt` before invoking an
offline resolver, and then performs a real dry run against the candidate
environment; the manifest pins `claude-agent-sdk==0.2.144`. `v2:eff-03` binds
its RC denial and recovery paths to the conservative nearest-rank p95 grader.
The live usage/soak portion of EFF-03 is not inferred from those unit controls:
it is a mandatory invariant of the runtime campaign below.

Each result binds the contract hash, complete catalog hash, exact plugin and
host SHAs, SDK version, sanitized profile identity hash, runner version, and
tool inventory hash. Passing packets require exactly one terminal event plus
both primary and secondary proof hashes. Consequential, initially failing, or
unstable paths require three consecutive passes with one unchanged candidate
identity. The grader reports `pass@3` and strict `pass^3` separately.
For every successful positive or recovery packet, normalized event kinds must
exactly match the catalog's `expected_trace`; a proof hash attached to the wrong
trace grades as a verified failure. Expected denials must end in one explicit
denied terminal.

## Runtime release-ready receipt

The runtime executor will not start from a branch name, editable install, or
unverified artifact. `HERMES_PARITY_RELEASE_READY_RECEIPT` must point to a
sanitized JSON document conforming to
`runtime-release-ready-receipt.schema.json`; `HERMES_PARITY_IMMUTABLE_WHEEL`
and `HERMES_PARITY_WHEEL_SHA256` must identify the same regular wheel file.
The receipt binds issue #9, exact plugin/host SHAs, SDK version, contract and
catalog hashes, and the immutable wheel digest. It contains no credentials,
prompts, sessions, or customer data.

The campaign performs 100 main-session turns, injects and recovers from a host
tool denial, checks memory and cross-runtime state fencing, uses one image,
executes exact-once synthetic write and cron mutations, starts one native Agent
background task, closes and resumes at turn 50, and verifies process teardown.
Only the 100 main-session turns count against the runtime budget; fail-closed
state and teardown probes do not reach the provider.

Every completed live turn contributes one safe usage sample. The campaign
fails unless nearest-rank p95 non-cache input share is at most 25 percent. It
writes one `runtime-usage-summary.json` containing only aggregate token counts,
the threshold/result, exact candidate hashes, and its own digest; cache-read
and cache-write traffic are reported separately. Raw prompts, responses,
sessions, identities, and per-turn transcripts are never written.

`result-packet-v3.schema.json` is the portable shape. Python validation is
intentionally stricter: it recalculates trace, candidate, and packet hashes;
rejects unsafe billing, invariant violations, silent fallbacks, forbidden raw
fields, and duplicate trials; and never treats `PARTIAL` as a gate result.

## Proof boundary

Source inventory or deterministic tests do not prove a live RC. RC evidence
can establish `release_ready` only for one exact plugin/host artifact pair.
The runtime lane can establish `runtime_safe` only for the isolated
`fable-v3-isolated` profile and immutable wheel used by the campaign. Neither
claim authorizes merge, publication, shared Eva, fleet, customer, or future-SDK
use.
