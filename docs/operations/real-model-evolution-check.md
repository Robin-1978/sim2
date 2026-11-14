# Real-model evolution check

Use the short evolution check when validating God → Platform → NPC → Story.
It exercises one dynamic Action and its bound behavior, advances the action to
completion, verifies the conserved world-state change, and runs Story on the
result. It does not simulate a town for days.

Always start the provider check from an **interactive bash** and source the
user environment first. The API key is read by the provider and must never be
printed or written to an artifact:

```bash
bash -ic 'source ~/.bashrc && python scripts/verify_dynamic_evolution.py \
  --provider remote \
  --runtime-dir /tmp/sim2-dynamic-evolution-check'
```

If the provider is unavailable, check only whether the environment variable is
present, without printing its value:

```bash
bash -ic 'source ~/.bashrc; test -n "${MY_MY_API:-}" && echo key-present || echo key-missing'
```

The short check prints a JSON audit containing the God tool calls, the bound
NPC behavior, the public-resource quantity before and after execution, the
carried quantity left on the NPC, and the Story result. Its temporary SQLite
instance belongs under `/tmp/sim2-dynamic-evolution-check*`; do not copy it
into the repository.

For fast local regression, run only the evolution tests:

```bash
python -m pytest -q \
  tests/test_action_binding.py \
  tests/test_npc_evolution_loop.py \
  tests/test_institution_evolution.py
```

The full suite is a pre-merge check, not the per-edit evolution check. The
short tests verify both a valid conserved transfer and rejection/rollback of
an attempted over-transfer.
