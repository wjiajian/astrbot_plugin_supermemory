# Repository Guidelines

## Project Overview

This repository contains `astrbot_plugin_supermemory`, an AstrBot plugin that integrates Supermemory as scoped long-term memory for private chats and group chats.

The plugin should stay lightweight:

- Do not modify AstrBot core.
- Do not add a custom WebUI.
- Use AstrBot plugin config via `_conf_schema.json`.
- Use Supermemory native HTTP APIs through `httpx`; do not add the official SDK unless there is a clear need.

## Important Files

- `main.py`: AstrBot plugin entrypoint, LLM hooks, `/supermemory` commands.
- `scope.py`: private/group scope generation and hashed `containerTag` construction.
- `supermemory_client.py`: async Supermemory API client.
- `memory_formatter.py`: formats recalled memories for prompt injection.
- `commands.py`: local salt/state store and command helpers.
- `_conf_schema.json`: AstrBot plugin config schema.
- `metadata.yaml`: AstrBot plugin metadata.
- `tests/`: unit and static contract tests.

## Memory Isolation Rules

Keep these names stable unless the user explicitly requests a migration:

- Private: `astrbot_private_<platform>_<sender_hash>_<umo_hash>`
- Group shared: `astrbot_group_shared_<platform>_<group_hash>_<umo_hash>`
- Group member: `astrbot_group_member_<platform>_<group_hash>_<sender_hash>_<umo_hash>`

Group chat recall uses two layers:

- `group_shared`: group-level shared context, no sender hash.
- `group_member`: current group member's personal context, includes sender hash.

Group member memory must not be shared across different users in the same group.

## Commands

User-facing command group is fixed as `/supermemory`:

- `/supermemory status`
- `/supermemory recall <query>`
- `/supermemory on`
- `/supermemory off`
- `/supermemory help`

## Development

Run tests with bytecode disabled to keep the worktree clean:

```powershell
python -B -m unittest discover -s tests -v
python -B -m py_compile main.py commands.py scope.py memory_formatter.py supermemory_client.py
```

The test suite should pass without real Supermemory credentials; API behavior is tested with `httpx.MockTransport`.

## Editing Notes

- Keep file edits ASCII unless existing content requires Chinese text.
- Do not commit or keep generated `__pycache__/` files.
- Do not log raw user IDs, group IDs, or API keys.
- Preserve hashed identifiers and local salt behavior in `scope.py`.
- If changing public config keys, update `_conf_schema.json`, `README.md`, and static contract tests together.
