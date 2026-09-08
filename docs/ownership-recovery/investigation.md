# Claude subscription / Hermes ownership feasibility

Date: 2026-09-09. Claim class: **advisory investigation**, not release or runtime readiness.

## Conclusion

The maintainer's five objections are supported. They are not all intrinsic limitations of subscription inference: tool fidelity, canonical visible-history replay, persistence and ordinary Hermes hook integration have concrete repair paths. The architecture should be a model-provider client inside Hermes' existing loop, not a second whole-turn runtime. The present DirectSDK is useful existing work toward that boundary, not a finished replacement.

The remaining strict per-generation admission requirement is a **reproduced native transport blocker** on both CLI 2.1.258 and 2.1.263. No documented supported pre-request gate or recovery-off control was identified in the inspected public interfaces. This is not proof of universal impossibility, of every hidden option's absence, or of future SDK behavior. It is sufficient to reject a claim that the current flags guarantee Hermes ownership of every generation.

High confidence applies to this bounded diagnosis and repair classification. No numerical probability or 95% success promise is assigned to an unimplemented remedy. Only a boundary prototype and its acceptance results can close that uncertainty.

## Frozen evidence identities

- Published plugin: `5e2230952174ee773280cf44cd6cae018a502afc` (v0.1.0).
- Qualified Hermes runtime host / PR101052: `80332e62eb19e48ed4a1c220dc4c06fe343418ac`.
- DirectSDK / PR105863: `eafb4186a4e8be7ac0ca94d69b10c57e0154aee8`.
- SDK: 0.2.151. Its native CLI 2.1.258 SHA256: `b63136194160791c27cfa7b0403060d85eb0752991625fde8c09f9acacb17c78`.
- Native CLI 2.1.263 SHA256: `ef5d2909c8af49f31ab6d5487e90316777bc2fac170adfe8160716caa8aaf4f9`.
- GitNexus `hermes-agent`: indexed `bdc5b1f74c26e6240deb8f067ade6dc91c00e8f2` on Aug20. Graph used for orientation only; these candidate flows are verified from exact source.

No source fixes, installed user-runtime changes, real provider calls, credentials, GitHub mutations, or release changes occurred. A disposable audit worktree and synthetic local evidence were created. Historical qualification remains unchanged and cannot answer the newly identified requirements.

## Independent native reproduction performed in this investigation

The maintained `evals/directsdk_cache_wire.py` loopback peer was reused. A small evidence-only subclass changes the synthetic response `stop_reason`; production adapter and executable bytes remain unchanged. Each client performs one `create()` with a fresh temporary home and no real credentials. Outbound network is OS-restricted to localhost, with dead proxies as an additional restriction. The process retains `--max-turns 1`, zero HTTP retries, zero native tools, no session persistence and disabled autocompaction.

| Native version | Normal end_turn | One max_tokens, then end_turn | Every response max_tokens |
| --- | ---: | ---: | ---: |
| 2.1.258 | 1 HTTP Messages request | 2 HTTP Messages requests | 4 HTTP Messages requests, then error |
| 2.1.263 | 1 HTTP Messages request | 2 HTTP Messages requests | 4 HTTP Messages requests, then error |

The fixture generated 14 localhost inference-shaped requests and **zero real provider requests**. Each client closed, registered requests reached zero and each peer stopped. The first attempt failed before native execution because macOS requires `localhost` rather than a literal IPv4 address in the sandbox rule; that syntax was corrected once. This is environment recovery, not a product fix or provider retry.

Receipts: [native-recovery-receipt.json](native-recovery-receipt.json), [probe](native_recovery_probe.py). Results demonstrate recovery in the actual binaries, not seventeen real calls inferred from injected usage metadata. They do not prove subscription entitlement, billing, real-model quality, all recovery paths, all platforms or SDK callback timing.

## Gap-by-gap repair map

### 1. Model hooks, shared budgets and generation controls

**Verified cause:** host `agent/conversation_loop.py:1483` returns to `run_registered_runtime` before the loop budget guard and normal preparation/request/response phases. `RuntimeTurnRequest` lacks the relevant iteration/shared-budget and generation fields.

**Smallest coherent repair:** use the existing model-provider `create_client` surface so Hermes performs its normal preparation, `agent:step`, pre/post request hooks, budget admission, tool execution and continuation. Keep authentication/transport in the plugin. Do not duplicate the loop or merely fire one pair of hooks around an entire SDK turn. Expose only generation controls actually supported by the selected model; unsupported controls must be declared, not silently promised.

**Evidence of feasibility:** DirectSDK registers a client under `api_mode=chat_completions`; its adapter handles output caps, reasoning configuration, streaming, cancellation and tool-batch return. It still has the native-recovery exception below. Thus ordinary logical-step hooks are feasible; complete per-generation budget control remains conditional.

**Acceptance:** instrument the real ordinary Hermes loop against the existing local recording peer. Every admitted generation has the correct hook order and shared-budget debit; budget zero permits no Messages request, including delegation/auxiliary consumers. Force output-limit recovery and require that no second request reaches the peer without a new admission. A callback after a request is sent fails this test.

**Difficulty:** high architectural change, several focused engineering days if the underlying request boundary is available. Hook wiring alone is not the blocker.

### 2. Canonical history and Hermes compression

**Verified cause:** plugin `turn_input.py:185` selects the latest user message; the retained native session supplies prior context. Hermes owns the system-prompt snapshot, but prior Hermes edits/imports/compression do not replace native history. Plugin compatibility explicitly declares runtime-native compaction.

**Repair:** replay the current canonical Hermes history for each model step; do not use opaque native session history as source of truth. Use a fresh request-scoped process or an equally proven history-reset contract. Run Hermes compression before replay and disable native compaction. Preserve opaque signed provider blocks only while their canonical visible projection is unchanged; after edits, reconstruct visible content without stale signed blocks. Never fabricate provider signatures.

**Evidence:** DirectSDK `prepare_history` and its existing regression test implement this pattern. This investigation's pure-function probe verifies full earlier history, unchanged caller data, preservation of unchanged opaque blocks, and removal of stale blocks after a canonical edit. The PR reports separate live edited-history and long-context/cache evidence; those live results were read, not rerun here. Native replay's `shouldQuery:false` acknowledgments and injected annotations remain version-sensitive; the Python SDK's public query abstraction does not by itself establish arbitrary canonical replay parity.

**Important qualification from independent review:** the tested edit is a substantive text change, not every possible canonical edit. DirectSDK normalizes assistant text with `.strip()` when comparing carrier projections (`directsdk.py:43,95`). A separate pure-function check confirmed an edit from `Reading` to ` Reading ` incorrectly replays the old `Reading`. Exact canonical fidelity therefore still needs a small comparison correction and direct regression proof in that implementation too. This does not make replay impossible; it prevents treating the existing implementation as already fully correct.

**Acceptance:** at the local wire, compare canonical user/assistant/tool content before and after import, assistant/tool edits, compression, resume and provider switch; unchanged prefixes remain stable, edits take effect, no stale carrier is restored, and no replay frame triggers inference. Then a small actual-subscription canary validates the supported edited-history path. Cross-model signed replay remains separately unproven.

**Difficulty:** medium/high, shared with the provider-client refactor. Existing code is available to adapt, not a reason to rebuild it independently.

### 3. Intermediate commentary and complete visible persistence

**Verified cause:** runtime content is streamed by `RuntimeHostAdapter.emit_content`; plugin completion returns original messages plus final text (`runtime.py:606`). Canonical tool calls/results ARE persisted by the actual Hermes executor. Missing commentary must not be described as missing all tool history.

**Repair:** return the complete assistant message for each ordinary model step and let the canonical loop persist it alongside tool-call/result rows. If maintaining the legacy runtime temporarily, accumulate/deduplicate complete visible assistant messages in event order, then commit them through existing host persistence. Keep evidence redaction separate from legitimate user-visible content; never turn partial transport chunks into duplicate conversation turns.

**Acceptance:** commentary -> two tool calls -> two results -> final answer; DB/reload/export/next-request representations preserve that order and content exactly once, including cancellation and resume. No requirement to retrieve hidden chain-of-thought.

**Difficulty:** medium; roughly one focused day for a localized correction, more if done inside the architectural refactor. This is an estimate, not a validated delivery time.

### 4. Honest accounting

**Verified cause:** `runtime.py:616` hardcodes host `api_calls=1`. Native `num_turns` is retained by event projection but is not a count of HTTP requests; assigning it directly to `api_calls` would substitute one misleading counter for another.

**Repair:** distinguish host-admitted model steps, observed native generations, transport attempts/retries, tokens and estimated cost. Reuse existing usage fields where semantics match; report unknown rather than invent unobserved requests. A single-generation boundary makes the ordinary host step counter meaningful again. Metered list-price equivalents are not subscription invoices or proof that extra usage is disabled.

**Acceptance:** normal, recovery, interrupted/error and missing-final cases reconcile observed peer requests and returned usage without double-counting cumulative receipts. An injected `num_turns=17` must remain explicitly native turns, not silently become one or seventeen verified network requests.

**Difficulty:** low for correcting misleading reporting; medium/conditional for exact internal request accounting. Accurate reporting does not prevent unadmitted work.

### 5. Tool result, description and schema fidelity

**Verified cause:** plugin `_bounded_text` collapses whitespace; descriptions cap at 4KiB, results at 64KiB; `_valid_schema` accepts only a subset of JSON Schema. A synthetic Python result is corrupted from multiline code to `def f(): x = 1 return x`; a schema containing `pattern` is rejected.

**Repair:** transport the host's already-authorized strings and schema faithfully. Retain Hermes' own tool-policy, approval, output-size and secret boundaries; remove accidental plugin-specific transformations. Separate log/evidence sanitization from model payload. Map unsupported schema/model features explicitly and fail clearly instead of dropping constraints. Do not promise arbitrary schema dialect support or unlimited provider payload sizes.

**Evidence:** this investigation's pure-function probe shows the existing DirectSDK conversion preserves the same multiline tool result, multiline description and `pattern` schema that GA transforms/rejects. The PR reports large prompt/schema native wire tests. Pure conversion success does not establish every schema keyword's backend acceptance.

**Acceptance:** byte equality for host-approved multiline code, Unicode, descriptions and representative supported nested schemas at model-request payload; allowed-host oversized-result behavior preserved; invalid/unsupported schema returns a clear error. Hermes denial and executor hooks remain intact.

**Difficulty:** low/medium, hours to a focused day for known transformations plus narrow regression coverage. Backend-specific schema limits require explicit compatibility, not infinite parity.

### 6. Native output recovery / strict request admission

**Verified blocker:** both real native versions issued 1/2/4 localhost requests as above. `max_turns=1`, zero HTTP retries and disabled tools/compaction are not a one-generation guarantee. The current Python hooks and public TypeScript hook list provide no identified before-every-model-request admission point.

The installed Python SDK's custom `Transport` interface is raw process/service I/O (`write`, `read_messages`, `close`), not an HTTP inference interception point. Replacing that transport alone therefore does not establish native-request admission. Bounded string inspection of the exact 2.1.263 executable found native recovery identifiers but was not a control-flow audit and is not used to prove that all hidden switches are absent.

**Viable mechanism, not yet available/proven:** a vendor-supported single-generation mode or synchronous before-request hook, covering recovery, compaction, structured-output correction, auxiliary requests and fallbacks. It must allow denial BEFORE bytes are sent, and must expose truncation/partial-response completion so Hermes can decide whether to continue using canonical history and remaining shared budget. No binary patch is proposed.

**Conditional alternative:** an authenticated request-aware egress gate can theoretically admit/deny requests before forwarding. A process firewall or CONNECT tunnel alone cannot count individual Messages requests inside an encrypted persistent connection. A real gate requires no bypass path, concurrency-safe permits, cancellation, auth/header/protocol compatibility and secret-safe operation; it may expand the credential/network trust boundary. This investigation did not implement or prove that design, its OAuth compatibility or service authorization. It is not a quick supported fix and must not be sold as one.

**Not fixes:** larger output caps, detection after the second response, killing the process after streamed `message_stop` without a synchronous admission guarantee, tracking `num_turns`, or switching to paid API credentials. The last can solve a transport problem but fails this project's subscription objective. Raising caps may reduce incidence only.

**Acceptance:** one admitted request maximum under forced output limits, error recovery, tool batches and structured output; denied next generation is never forwarded; first response/usage/finish reason remain usable; no fallback or alternate egress; next Hermes-admitted step can continue with the correct canonical history.

**Difficulty:** conditional/vendor-dependent. No finite delivery promise. A request-aware proxy would be a separate security-sensitive project, not an automatic recommendation.

## What is impossible, versus not yet proven

1. **Proven inadequate under current settings:** strict one-generation ownership using the tested unmodified native binaries plus `max_turns=1` / zero retries alone. A concrete counterexample disproves that guarantee.
2. **Not established through current public controls:** complete before-send admission across every native recovery branch. We did not prove the absence of all undocumented controls or future vendor solutions.
3. **Not impossible in principle:** canonical history, Hermes compression, visible persistence, hooks, tools and honest usage reporting. Existing code/source and narrow probes substantiate repair paths, but do not qualify a finished successor.
4. **Cannot promise:** zero Claude-derived process while retaining this SDK transport; generation controls rejected by the model; unlimited schema/context/outputs; private provider reasoning/signatures reconstructed after edits; arbitrary future versions/platforms; subscription entitlement or legal/service-terms approval from technical tests.

## Recommended bounded next decision

Do not restart a broad parity campaign or stack hooks onto the whole-turn runtime. First resolve the missing native admission boundary with the transport maintainer/vendor. If a supported control is available, make the existing output-limit counterexample pass before investing in a successor. Then build on the ordinary model-provider work, retain the plugin packaging/auth benefits, and validate only affected canonical-history/tool/persistence paths followed by a small real-Hermes canary.

If no supported control is available, explicitly choose either waiting for that control or a limited opt-in integration that admits native recovery is outside Hermes' strict budgets. That is a product-scope decision, not a passing full-parity test. A request-aware egress prototype requires separate approval because of the new trust boundary. No source or GitHub changes are authorized by this report.

## Primary sources

- [Our host bypass](https://github.com/NousResearch/hermes-agent/blob/80332e62eb19e48ed4a1c220dc4c06fe343418ac/agent/conversation_loop.py#L1483)
- [Our input projection](https://github.com/100yenadmin/hermes-claude-agent-sdk/blob/5e2230952174ee773280cf44cd6cae018a502afc/src/hermes_claude_agent_sdk/turn_input.py#L185)
- [Our result/history accounting](https://github.com/100yenadmin/hermes-claude-agent-sdk/blob/5e2230952174ee773280cf44cd6cae018a502afc/src/hermes_claude_agent_sdk/runtime.py#L606)
- [Our tool conversion](https://github.com/100yenadmin/hermes-claude-agent-sdk/blob/5e2230952174ee773280cf44cd6cae018a502afc/src/hermes_claude_agent_sdk/tool_bridge.py#L535)
- [DirectSDK inspected source](https://github.com/NousResearch/hermes-agent/blob/eafb4186a4e8be7ac0ca94d69b10c57e0154aee8/plugins/model-providers/claude-oauth-directsdk/directsdk.py)
- [Maintained local peer](https://github.com/NousResearch/hermes-agent/blob/eafb4186a4e8be7ac0ca94d69b10c57e0154aee8/evals/directsdk_cache_wire.py)
- [DirectSDK public qualification and HOLD](https://github.com/NousResearch/hermes-agent/pull/105863)
- [Official Python SDK options/hooks](https://code.claude.com/docs/en/agent-sdk/python)
- [Official agent-loop limits](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [Official Python/TypeScript hook availability](https://code.claude.com/docs/en/agent-sdk/hooks)

## Historical investigation closeout

Independent bounded semantic challenge: the reviewer substantiated the core sources and 1/2/4 fixture evidence, then abstained because two report paragraphs were updated during review (custom Transport clarification and final readback). Review also identified the outer-whitespace carrier limitation above, independently reproduced by root. This corrected report is frozen for one targeted review of those report deltas; no repeated general audit or provider run. Final readback confirms PR101052 remains OPEN at `80332e6`, PR105863 remains OPEN at `eafb4186`, and the plugin/qualified-host/audit worktrees have no tracked or untracked source changes. No durable GitHub delta: investigation only.

## Publication correction — 2026-09-09

The final [advisory review receipt](review-receipt.json) binds the frozen report
and native evidence hashes. The targeted PASS covers the corrected report
paragraphs while retaining the initial review's unchanged-source coverage;
it is not a replacement runtime qualification.

This is a sanitized copy of the frozen investigation (original SHA-256 `311b76ef43e62667f1d98f6c92c24fa4237c21eedd4cb468ecd78851a296b04f`), not a new qualification. The final independent review accepted the corrected advisory report. The selected recovery route is a supported vendor admission mechanism; proxy experiments, credential interception and binary patches are excluded. See [tracker #1](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/1). Historical releases and evidence remain immutable. Full Hermes ownership/parity is currently unproven.
