# Fitness tool contracts

Lightweight version 1 Java Tool Gateway DTOs, fixed 20-tool catalogue and exact request-body builders.
No imports from the Agent, LLM, SQL, checkpoint or confirmation storage packages.

- `models`: existing `Gateway*` response DTOs.
- `inputs`: versioned business inputs with explicit aliases and bounded IDs.
- `catalog.TOOL_SPECS`: 11 read tools and 9 confirmed writes, input/output schemas and stable fingerprints.
- `commands.build_gateway_command(tool_id, arguments)`: fixed route, query and immutable canonical body.
- `canonical`: the same UTF-8, sorted-object-key encoding used by confirmation signing; arrays retain order.
- `agent_context` / `jwks`: verify existing two-segment signed assertions. RS256 uses only public keys.

MCP names retain dotted business tool IDs. `fitness.operations.metric.aggregate.v1` represents one fixed
aggregation. The Agent keeps comparison, question consistency, audit and budget orchestration locally.

Read arguments use the existing Gateway method's snake_case parameters. Writes carry `payload`, plus
`plan_id` / `day_id` when the Gateway method takes them. Builders validate payloads without inserting
missing defaults or rewriting already approved JSON values. Submit/publish bodies are exactly `{}`;
training day execution maps to `dayId`, `status`, `note`.

`gateway_confirmation_body` retains the legacy two-argument body mapping; new transport version 1
confirmation should use `build_gateway_command(...).body` with the fixed remote argument mapping.
