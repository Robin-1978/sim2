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

## 3. Director is separate

The Director is normally a deterministic service. It does not require an LLM
to tick. A separate `roles.director` provider is used only when a caller invokes
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

## 5. Reproduction

```bash
python -c "from sim2.llm.config import LLMConfig; c=LLMConfig.load(); print(c.provider_name_for('director'), c.role_for('god'))"
python scripts/verify_dynamic_social_content.py --provider remote --runs 1 --broad
python scripts/run_full_social_loop.py --provider remote --rounds 2
```
