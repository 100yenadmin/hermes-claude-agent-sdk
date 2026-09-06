# Removal and rollback — local Revision 4 install

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

Disabling the plugin keeps built-in Hermes behavior available. Generic Hermes
state stays inert and is not destroyed; no SDK session or provider route is
replaced by a native Claude path.

Removing the plugin must unregister its runtime through the Hermes plugin
lifecycle. Disable it first, then remove only the package and configuration
created for this plugin. Do not delete Hermes state, transcripts, or shared
Claude login data.

```sh
python -m pip uninstall -y hermes-claude-agent-sdk
```

Rollback of this local candidate is to the previously approved local plugin
artifact, or to plugin absence. Record the artifact digest locally, disable the
current entry point, install the chosen prior artifact if one exists, and
verify registration plus built-in Hermes operation. This procedure is local
recovery evidence only; it does not claim a merge, release, future-version
compatibility, or customer readiness.
