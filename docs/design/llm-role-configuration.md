# LLM Role Configuration

Status: **active local configuration recorded for reproducibility**
Example: [`examples/llm-config.example.json`](../../examples/llm-config.example.json)

The active local file is `~/.sim2/llm.json`. A timestamped backup is created
before edits. Secrets are referenced through `api_key_env`; no key is stored
in the repository or in this document.

## 1. Providers

| Name | Type | Model | Notes |
| --- | --- | --- | --- |
| `local` | OpenAI-compatible | `local-model` | `http://127.0.0.1:8192/v1`; optional offline fallback |
| `remote` | OpenAI-compatible | `deepseek-v4.1-flash` | default for active roles |

## 2. Roles

| Role | Provider | Max completion tokens | Max iterations | Max read calls | Reason |
| --- | --- | --- | --- | --- | --- |
| `director` | `remote` | 1024 | 4 | 2 | Chooses one structural target; no large catalog needed |
| `god` | `remote` | 4096 | 24 | 4 | A structural pack may contain many proposals; output must fit full definitions |
| `story` | `remote` | 2048 | 8 | 4 | Reads validated evidence and writes an emergent beat |
| `agent_decision` | `remote` | 768 | 8 | 3 | One behavior choice after bounded reads |
| `agent_reflection` | `remote` | 1024 | 4 | 2 | Short durable lesson |
| `world_builder` | `remote` | 4096 | 8 | 4 | Generates world configuration |

`default_provider` is `remote`. The `local` provider remains configured but is
not the default for active roles.

For the LLM Director, only the completion-token limit applies. It makes one
`complete_json` call with a 28-character system message and a 193–323 character
target/count instruction, and receives no tools. Its iteration/read columns are
kept for schema compatibility with other roles but are not used on this path.

## 3. Director is separate

The Director is normally a deterministic service. It does not require an LLM
to tick. A world opts into the LLM showrunner with:

```yaml
director:
  cadence: 1d
  window: 3d
  targets: [place, verb, social, rule]
  policy: llm
```

With `policy: llm`, `SimulationRunner` calls `plan_with_provider` once at each
cadence boundary, before the world tick, so the model decision never runs
inside the tick transaction. A provider error, an invalid target, or a missing
`director` provider falls back to the deterministic least-cell policy in the
same minute. `policy: deterministic` is the default and makes no Director model
call.

The separate `roles.director` provider is also available to probes through
`DirectorRuntimeMixin.plan_with_provider`. That method:

1. builds the allowed target list and current cell counts;
2. asks the Director model for one target cell;
3. validates the target against the configured list;
4. writes the same `season_plan` and `god_structural` task as the deterministic
   path.

Actual verification run:

```json
{
  "plan_id": "plan:5:resource",
  "target_cell": "resource",
  "baseline": 0,
  "reason": "Resource is the only available structural target and currently empty, so it must be selected to establish the structural foundation.",
  "status": "active"
}
```

## 4. Why these values

- `god` needs 24 iterations because a contract can contain 15 atomic stages,
  plus bounded reads and corrections. The engine also raises the non-explicit
  structural budget to 20.
- `god` needs 4096 output tokens because one proposal may contain a full Action
  and a follow-up behavior tree.
- `agent_decision` no longer relies on the small local model for important NPC
  behavior; 8 iterations allow one or two reads before the final choice.
- `story` is explicitly remote instead of inheriting the default provider.
- `director` has a small budget because it selects one cell, not content.

## 5. Function schemas and cache

Function schemas are part of the provider's cacheable prefix, not the dynamic
prompt. Keep them stable per role and per task class so repeated calls hit the
provider cache; do not rebuild the schema from changing context on every turn.
Every model-facing description is capped at 120 characters and contains no
truncated `...` text; field names, types, enums and required lists carry the
meaning instead of long prose.
The measured God contract schema is 10,755 chars (~2,684 tokens) after
compression, and real remote runs show a 79.5% cached-token share for God,
55.1% for `agent_decision` and 47.7% for Story. Details and the schema-size
audit are in [context-scaling-plan.md](context-scaling-plan.md).

The probe budgets must match the role configuration. The old decomposed probe
used 3 iterations and 1 read and produced artificial `read_budget` /
`tool_budget` rejections. It now uses 24 iterations, 40 tool calls and 4 reads,
the same envelope as the God role.

## 6. Reproduction

Set `providers.<name>.context_window_tokens` to the window available at the
endpoint, including output. The default is 32,768. The request budget includes
the system message, the rendered snapshot, conversation messages, native tool
and response schemas, configured output tokens and estimation headroom. World-level
`context_limits` still apply to the snapshot. `context_budget` is a retryable
preflight rejection. The provider receives no request if the required context
and output reserve exceed the budget. Token estimates use UTF-8 and text
length. Provider-reported usage gives the measured token count when available.

```bash
python -c "from sim2.llm.config import LLMConfig; c=LLMConfig.load(); print(c.provider_name_for('director'), c.role_for('god'))"
python scripts/verify_dynamic_social_content.py --provider remote --runs 1 --broad
python scripts/run_full_social_loop.py --provider remote --rounds 2
python scripts/verify_agent_outcomes.py --provider remote --runs 3
```
