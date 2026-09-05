# Installed Hermes session handoff — Revision 4

The component handoff uses the real installed Hermes TUI/desktop gateway. It
does not use a synthetic parity host, a separate native Claude Agent route, or
an SDK-owned transcript. Hermes owns the visible session and effects.

## Exact host contract

The `0.1.0rc1` plugin candidate is identified by the exact source commit and
wheel digest recorded in its v4 result manifest. It targets the Hermes host at
exact commit `80332e62eb19e48ed4a1c220dc4c06fe343418ac` (the original Revision 4
baseline was `15039e4f2d096b06f56369fbd78be09f3be73065`). The installed data-plane
entrypoint is:

```sh
python -u -m tui_gateway.entry
```

Hermes' desktop and TUI clients speak newline-delimited JSON-RPC to this
process over standard input and output. The session store is the existing
SQLite database at `<HERMES_HOME>/state.db`; the plugin does not add a second
store.

## Required image setup for the subscription model

Select `claude-agent-sdk` with `claude-fable-5-1` in the intended Hermes
profile. Also declare this model's vision capability using the existing
Hermes configuration command:

```sh
hermes config set model.supports_vision true
```

Run this against the intended profile (or with its explicit `HERMES_HOME`),
not an unrelated default profile. This is model configuration, not a new host
API or plugin-owned tool. It lets Hermes send native image parts through the
same subscription-authenticated model transport. No auxiliary paid vision
provider is needed. Do not retain this model-level override when switching
the profile to a model that cannot accept images.

Without the capability declaration, Hermes' automatic model catalog may not
recognize the standalone subscription provider/model. It then supplies a
text-only attachment reference for `vision_analyze`, rather than the image
bytes. That does not prove native image support and must not be silently
accepted as a pass or routed through a paid fallback.

The installed setup proof must check the image answer, the saved `@image:`
attachment reference and its file checksum, normal visible-session state,
subscription-included billing, and zero auxiliary tool calls. Hermes may
persist the image as a file reference instead of embedding image bytes in
the conversation database. Current qualification and immutable artifact
identity are recorded in [release issue #9](https://github.com/100yenadmin/hermes-claude-agent-sdk/issues/9).

## Zero-model session sequence

1. Install the exact plugin wheel into the exact Hermes environment.
2. Run `hermes plugins enable claude-agent-sdk --no-allow-tool-override` in an
   isolated profile or task-local `HERMES_HOME`.
3. Start `python -u -m tui_gateway.entry` and wait for `gateway.ready`.
4. Call `session.create`. Retain its `stored_session_id` as the durable Hermes
   identity; the returned `session_id` is only the live gateway handle.
5. Call `session.title` against the live handle. Hermes intentionally creates
   no database row for an abandoned empty draft, so this explicit title call
   is the zero-model materialization step.
6. Call `session.list` with the exact title and `include_hidden: true`. This is
   the desktop's canonical registry lookup and exposes the hidden zero-message
   session without relying on the ordinary global-recents view.
7. Stop the gateway, start a fresh one against the same isolated
   `HERMES_HOME`, repeat the exact-title lookup, and call `session.resume` with
   `lazy: true` and `omit_messages: true`.

The repository test
`tests/test_installed_hermes_session_contract.py` executes this sequence with
credential variables removed and provider HTTP proxies pinned to a closed
local port. It verifies entry-point metadata, enables the plugin through the
public CLI, runs the offline compatibility doctor, and proves registration
does not import `claude_agent_sdk`.

```sh
python -m pytest tests/test_installed_hermes_session_contract.py
```

The test emits no raw session identifiers. A pass proves installed component
discovery plus Hermes session creation, persistence, exact-title visibility,
and lazy resume. It does not prove a Claude/Fable turn, subscription billing,
tool execution, model output, shared Eva operation, merge, publication, future
compatibility, or customer readiness.

## App visibility and runtime continuation

Before the first user message, the hidden session is visible through the
desktop's exact-title canonical-session lookup. A later Hermes turn supplies
the direct prompt, exact tool inventory, and approval policy to the SDK adapter.
The supported surface has `tools=[]`, `setting_sources=[]`, and no Claude-native
Agent/background route; delegation is Hermes `delegate_task` and detached
completion is host-owned.

Rollback is unchanged: disable `claude-agent-sdk`, uninstall the exact plugin
artifact when removal is required, and leave `state.db` intact. Generic
runtime state remains inert while the plugin is absent, and built-in Hermes
operation remains available. This handoff is local installed evidence only; it
does not claim merge, release, future compatibility, or customer readiness.
