# Hermes release acceptance — 2026-09-05 correction

## Decision and authority

The owner directed a bounded correction of the benchmark-driven release gate.
The product goal is unchanged: Claude subscription authentication in normal
Hermes, with Hermes owning the agent and the SDK supplying model transport.
This document supersedes **only the blanket native-36 / 220-path / 390-packet
release obligation** in milestone 1 and its issues. It does not declare a pass
on that benchmark or reduce the supported Hermes behavior or safety floor.

The [v4 catalog](parity-contract-v4.yaml), SHA-256
`53864834496403388f3475291475fea70acfa3105609ad49f5edf75ad1c67d94`, its
124 source rows, graders, schemas, scores, repeat sets, and original evidence
remain immutable. The old aggregate may correctly remain `PENDING` forever.
Do not edit it to manufacture `220/220`, convert exclusions to passes, or feed
the corrected release decision to its old fixed-count receipt validator.
There is no new runner, grader, framework, or replacement benchmark.

GitHub [tracker #1](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1)
owns current status. [#14](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/14)
owns supported-behavior evidence admission; [#9](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/9)
owns the protected `release_ready` readback. This document is acceptance policy,
not another roadmap or an assertion that those issues are complete.

## Required release proof

Use existing normal Hermes execution and canonical state as primary evidence.
Use deterministic tests for races, error injection and forbidden operations;
do not force extra model calls to reproduce a deterministic boundary.

| Gate | Required behavior and evidence |
| --- | --- |
| H1 — subscription and ownership | Exact SDK/CLI/model and subscription-included billing; no API-key, Extra Usage, metered or fallback route. Captured `tools=[]`, `setting_sources=[]`, direct Hermes prompt, strict Hermes MCP allowlist and zero native Claude tool/Agent events. Compatibility is checked before provider use. |
| H2 — normal Hermes session | Installed plugin, non-hidden normal Gateway session, text and image input, Hermes-owned prompt/project context, tools, approvals, persisted messages/results, resume, skills, memory recall and isolation. Model claims alone are not proof. |
| H3 — tools and policy | Compare the real configured Hermes inventory and delivered SDK schema hashes; unknown, missing, extra or changed tools fail closed. Real host execution, denial before side effect, recovery, request deduplication and persisted results. Synthetic five-tool coverage is not full registry coverage. External services unavailable in the isolated profile are explicitly unavailable, not tested passes. |
| H4 — delegation and background | Actual Hermes `delegate_task` handoff, two-child fanout/synthesis, stale/foreign-child isolation, background settlement and parent attribution; all child/result/terminal events in Hermes state, exactly once. A planning document is not delegation proof. |
| H5 — lifecycle and errors | Preserve all v4-mapped v2 non-soak, active-12 and boundary-23 obligations: 53 + 36 + 23 paths and their 222 packets/repeats. These cover cancellation, resume/fork/reuse, approval races, compaction/mutation safety, model/tool continuity, usage and errors. Preserve unknown values; cumulative usage is not context occupancy. Exact prose, hidden reasoning and identical provider token accounting are not required. |
| H6 — real host capabilities | Reuse actual Hermes file/terminal, browser, skills, history-search and isolated scheduler evidence. Check availability and persisted effects using Hermes semantics. Do not introduce a foreign CLI, directory API, job-expiry policy, agent catalog or messaging route to satisfy a borrowed task. A genuine supported Hermes gap still blocks until fixed or explicitly decided by the owner. |
| H7 — artifact and delivery | Bind one plugin source, immutable wheel/payload manifest, host SHA, SDK/CLI/model, this acceptance document hash, preserved catalog hash, runner and each isolated profile/inventory. Require install/upgrade/uninstall/reinstall/rollback/doctor, exact-head applicable plugin and host checks, terminal dispositions of verified current-path review findings, and an upstream-ready compatibility handoff. No merge or publication is authorized. |
| H8 — independent confidence | Two blind read-only acceptance/adversarial checks must each return `PASS` at least 95, based on the stable candidate and H1–H7 evidence. Scores are not a statistical probability; missing evidence, disagreement or lower scores mean NOT HIGH CONFIDENCE. No speculative hardening or replacement reviewers. |

All 88 core source items remain mandatory. Existing completed evidence is reused
only after identity and affected-code/profile checks. Required strict `3/3`
repeats remain strict; `pass@3` is never a substitute. Stable supported tool
smokes run once unless a reproduced defect invalidates them. Native benchmark
results supplement this proof; their scores are not the shipment denominator.

## Disposition of the ten unfinished borrowed cases

The following is a release-applicability decision, **not a benchmark pass**.
Original failures and unexecuted paths stay as recorded. Reuse identifies the
Hermes behavior already covered; final evidence admission must bind its exact
receipt and scope. No unresolved safety finding is waived.

| Original source item | Disposition and Hermes proof obligation |
| --- | --- |
| `constraints_23_external_approval_boundary_live` | Keep the extra declaration/rendered-content predicate unresolved as reference-only. It did not establish a Hermes approval bypass or leak. H1/H3 still require actual approval denial/recovery, late-approval rejection, scope fencing and exact-once effects; use the installed canary, core boundary proof and audience-boundary controls. Reopen only on a concrete supported-path counterexample. |
| `error_recovery_22_incident_commander_sequence_live` | Foreign fixture/plan-format expectations are not a transport feature. Reuse real browser/status reads, denial/recovery and persisted outputs. Do not invent expected answer fields or claim that an incident plan executed containment. |
| `intel_h02_cross_surface_diagnosis` | Replace OpenClaw status-CLI composition with observed Hermes tool availability, actual multi-surface reads and explicit error/unavailable state. Reuse the installed skills/history/browser probe and host error evidence. |
| `intel_m06_session_health_check` | OpenClaw session counters/default-agent inventory are not a Hermes API contract. Retain Hermes identity/usage/error proof and verify unknown context occupancy is not represented as zero or healthy. No fabricated context value or new status API. Any reproduced Hermes-visible reporting regression is an H5 blocker, not an excluded benchmark detail. |
| `intel_x01_full_system_audit` | A foreign full-system audit is not the plugin boundary. Reuse real Hermes inventory/doctor, session lifecycle and supported tool smokes. Do not add a system-audit framework or claim credentials/services outside the test profile were exercised. |
| `planning_19_agent_delegation_boundary_live` | Foreign agent-catalog labels and plan-file spelling are reference-only. H4 remains actual Hermes handoff, child policy and parent-attributed results; reuse the installed canary and active/boundary packs. |
| `planning_20_session_agent_handoff_live` | Same H4 obligation. Preserve the original planning score; it neither proves nor invalidates already-executed Hermes delegation. No native Claude Agent route. |
| `synthesis_16_openclaw_runtime_surface_matrix_live` | OpenClaw channel-directory commands and its grader's ready/error convention do not define Hermes support. Reuse real inventory/history evidence; unavailable directory functions are not marked ready or passed. |
| `synthesis_17_openclaw_gateway_surface_matrix_live` | Reuse actual Hermes browser and scheduler availability/effects. Telegram/message dry-run conventions are reference-only; no external messaging, credentials or customer traffic is added. |
| `tool_use_21_recurring_cron_expiry_notice_live` | OpenClaw's seven-day default expiry is NOT APPLICABLE to API-backed Hermes. Keep the source row unfinished. Hermes scheduling uses Hermes policy, with existing real create/list/store/cleanup evidence. Never implement foreign expiry or equate 168 firings with seven elapsed days to clear this row. |

## Evidence reuse and truthful status

- Preserve original candidate/profile/runner identities, raw source grades and
  proof hashes. Use a short admission record linking old receipts to the exact
  accepted artifact; do not rewrite packets or invent a single historical
  profile. An isolated cohort family is allowed only with its member scopes
  explicitly listed and relevant code/configuration equality established.
- The completed core bundle has 112 paths / 222 packets. Native evidence at
  the correction checkpoint contains 26 completed sources / 78 paths / 120
  packets across separate cohorts. Their arithmetic total, 190 paths / 342
  packets, is an evidence inventory, not a final candidate grade or a claim of
  full ClawProBench compliance. The remaining ten sources are dispositioned
  above, not changed to passes.
- H7 must distinguish plugin CI against an exact host from that upstream host
  PR's own CI/review. Existing payload equivalence permits tests/docs-only
  source successors to reuse live proof, not to claim unobserved execution.
- Keep the original v4 validators fail-closed for their original contract.
  The corrected release record is the reviewed H1–H8 evidence readback in #9,
  not an altered old numeric receipt. Missing supported proof remains open.

## Runtime qualification and execution ceiling

[Milestone 2 / #15](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/15)
still requires the separate 100-turn same-session campaign, using the exact
`release_ready` artifact from #9 through normal Hermes. Include restart/resume
at 50, denial/recovery, memory isolation, an image, Hermes delegation/background
settlement and teardown. Require 100/100 expected outcomes, stable identity,
exact-once effects/terminals, no orphan processes and no billing/tool drift.
Only that evidence earns isolated-profile `runtime_safe`.

Before any further harness change, name the missing supported Hermes behavior,
the observed evidence gap, and why existing state/events cannot prove it. Make
one narrow correction or real-product check, then resume the gate. No broad
repeats, answer hints, new framework, idle soak, hidden-reasoning requirement,
merge, publication, shared-Eva/customer traffic or silent fallback.
