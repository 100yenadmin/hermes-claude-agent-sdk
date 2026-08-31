# Removal and rollback

Removing the plugin must unregister its runtime through the Hermes plugin
lifecycle. Built-in Hermes behavior, including the Codex whole-turn runtime,
must remain available. Generic runtime state stays inert and is not destroyed.

Operators remove only the plugin package and configuration they created. They
must not remove shared Claude login data or user authentication files.

Before the release candidate, rollback is to the exact clean Hermes base and
the absence of this plugin. Once a prior plugin artifact exists, rollback uses
its immutable wheel SHA-256: uninstall the current candidate, install the prior
artifact, verify registration, then verify built-in operation. A retracted RC
keeps its tag, history, checksums, evidence, and a visible compatibility warning;
the same tag is never moved to another SHA.
