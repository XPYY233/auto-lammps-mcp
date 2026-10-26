# Installation and setup

Follow the repository's `docs/FOR_AGENTS_INSTALL.md`. Detect local execution and MCP support. Reuse an existing installation. Use the actual executable path returned by the installer. Keep installation progress and give a copyable resume sentence when the host needs a new session.

For DSH, use the user's official DeepSeek Harness CLI/source installation and existing model configuration. Supply `--dsh-runtime` and the intended profile to the installer. Its managed MCP block preserves the profile's other settings and credentials. Record the actual runtime version and verify a real tool call from DSH. A retired custom desktop is not an installation target.

After installation or upgrade, check MCP doctor.current_connection. current confirms this conversation loaded the same candidate as the service. reload_required means reload the host MCP connection or open a new conversation, then call doctor again. unverified_client means the old bridge has no candidate receipt. Historical host_calls show the latest calls from each platform; they do not replace the current conversation check.

Discover existing SSH aliases, project directories and validated environments before asking for missing details. Keep passwords and keys in the platform's login or system credential flow. Distinguish installed package, live service, actual host tool call, cluster connection, compute-node environment validation and background Codex continuation.
