# Removal and rollback

## Disable before removal

Installing this package does not enable its entry point. The supported opt-in
command is:

```sh
hermes plugins enable claude-agent-sdk
```

To roll back without removing the package, disable the entry point:

```sh
hermes plugins disable claude-agent-sdk
```

Disabling the plugin keeps built-in Hermes behavior, including the Codex
whole-turn runtime, available. Generic runtime state stays inert and is not
destroyed.

Removing the plugin must unregister its runtime through the Hermes plugin
lifecycle. Disable it first, then remove only the package and configuration
created for this plugin.

```sh
python -m pip uninstall -y hermes-claude-agent-sdk
```

Do not remove shared Claude login data or user authentication files.

Before the release candidate, rollback is to the exact clean Hermes base and
the absence of this plugin. Once a prior plugin artifact exists, rollback uses
its immutable wheel SHA-256: uninstall the current candidate, install the prior
artifact, verify registration, then verify built-in operation. A retracted RC
keeps its tag, history, checksums, evidence, and a visible compatibility warning;
the same tag is never moved to another SHA.
