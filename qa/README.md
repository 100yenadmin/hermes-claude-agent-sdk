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

Each result binds the contract hash, complete catalog hash, exact plugin and
host SHAs, SDK version, sanitized profile identity hash, runner version, and
tool inventory hash. Passing packets require exactly one terminal event plus
both primary and secondary proof hashes. Consequential, initially failing, or
unstable paths require three consecutive passes with one unchanged candidate
identity. The grader reports `pass@3` and strict `pass^3` separately.

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
