#!/usr/bin/env bash
set -euo pipefail

provider="${1:-remote}"
runtime_dir="${2:-/tmp/sim2-dynamic-evolution-check}"
printf -v provider_q '%q' "$provider"
printf -v runtime_q '%q' "$runtime_dir"

# The provider key is intentionally loaded only inside the interactive shell
# and is never echoed or persisted.
exec bash -ic "source ~/.bashrc && exec python scripts/verify_dynamic_evolution.py --provider ${provider_q} --runtime-dir ${runtime_q}"
