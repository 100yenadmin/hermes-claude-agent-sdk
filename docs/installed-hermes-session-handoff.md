# Installed Hermes session handoff — Revision 4

The component handoff uses the real installed Hermes TUI/desktop gateway. It
does not use a synthetic parity host, a separate native Claude Agent route, or
an SDK-owned transcript. Hermes owns the visible session and effects.

## Exact host contract

The `0.1.0rc1` plugin candidate is identified by the exact source commit and
wheel digest recorded in its v4 result manifest. It targets the Hermes host at
exact commit `d09a65e56d340437b740f2fb09cef36d469616ab`. The installed data-plane
entrypoint is:

```sh
python -u -m tui_gateway.entry
```

Hermes' desktop and TUI clients speak newline-delimited JSON-RPC to this
process over standard input and output. The session store is the existing
SQLite database at `<HERMES_HOME>/state.db`; the plugin does not add a second
store.

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
