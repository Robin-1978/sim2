# Memory Closure Audit

Memory is not the event log. Platform keeps the authoritative event and state
projection in SQLite; an Agent receives a small, permission-filtered view of
facts and memories.

| Layer | Stored content | Lifetime | Retrieval use |
|---|---|---|---|
| World event | What Platform actually changed or observed | Permanent audit | Timeline, Story/God triggers, outcome review |
| Knowledge fact | What this Agent currently believes it learned, with source and confidence | Until revised or superseded | Direct decision facts |
| Short-term memory | Recent testimony, dialogue, inspection, trade, and working context | Bounded working set | Current task and next decision |
| Long-term memory | Reflection-approved reusable experience, belief, goal, or summary | No time decay; explicit compaction/revision only | Future decisions and Reflection |

Memory and knowledge records carry the same fixed retrieval dimensions:
location, participants, behavior, object, topic, and goal. The dimensions are
stored as a compact `tags` envelope beside the prose. They are soft ranking
features, never a hard candidate filter: permission/scope filtering happens
first, then BGE (or lexical fallback) scores every permitted candidate and the
matching tags add a small boost. SQLite FTS5 remains available for explicit
text queries.
When the instance config enables a local BGE-compatible model through
`SIM2_BGE_MODEL`, the same candidate set is batch-ranked by embedding cosine
similarity; if the optional model is unavailable, retrieval falls back to the
deterministic lexical score. The model is never allowed to mutate the world.
The adapter also accepts `SIM2_BGE_CACHE` or the BieTheirBed-compatible
`KNOA_BGE_CACHE` fastembed cache layout (`models--*/snapshots/*`), so an
already downloaded `model_optimized.onnx` and `tokenizer.json` pair is reused
without another download. The Starter Village preloads the model; other
packages can set `SIM2_BGE_PRELOAD=1` or `semantic_similarity.preload: true`.
The model is preloaded once per process. Candidate embeddings are also cached
in the instance SQLite database, so a new Agent session or process restart can
reuse vectors for unchanged text. A 128-candidate cold batch is about 0.15
seconds on the current CPU; after reopening the same SQLite instance it is
about 0.02 seconds. Query vectors remain in process memory only because they
are transient and should not grow the world database.
ONNX Runtime does not require PyTorch. GPU execution requires an
`onnxruntime-gpu` build exposing `CUDAExecutionProvider` plus a working CUDA
and cuDNN runtime; a CUDA-enabled PyTorch installation alone is not enough.
`SIM2_BGE_PROVIDER=auto` selects CUDA when that provider is available and
otherwise falls back to CPU. The backend status reports the provider actually
used.

## Persistence and scale status

The current implementation persists memory prose, tier, tags, importance,
salience, access counts, and content-hash keyed BGE vectors in SQLite. The
vector table is a rebuildable cache, never authoritative memory; its `model_key`
separates model/backend revisions. Retrieval ranks at most 1,000 permitted
records per tier, but BGE reranking is bounded by `embedding_candidate_limit`
(256 by default). FTS/lexical ranking produces that candidate set first.

LanceDB is intentionally not required for the current scale. Its current
[vector-index guidance](https://docs.lancedb.com/indexing/vector-index) says
that exact disk kNN is normally sufficient at roughly 100,000 rows or fewer;
ANN index overhead becomes worthwhile closer to million-row/high-dimensional
workloads or strict latency targets. A sim2 world currently has tens to
thousands of permitted records per Agent and must remain one portable SQLite
file. LanceDB would add `lancedb`/PyArrow dependencies and a second storage
directory while still requiring BGE to encode every new query. If a future
world exceeds the bounded scan, LanceDB can be added behind the same retrieval
interface and measured against exact recall before it becomes the default.

At roughly 10,000 memories per Agent, SQLite storage is not the limiting risk;
candidate generation, encoding time, and prompt size are. The planned query
path is permission scope → FTS/dense candidate generation → BGE ranking →
tag soft boost → bounded prompt slice. Tags must never hard-filter a semantically
relevant memory. `sqlite-vec` can be evaluated later as an optional accelerator,
but it is not required for correctness.

A controlled ten-query paraphrase check over Starter Village concepts found
Recall@1 of 2/10 for the lexical fallback and 8/10 for BGE when the relevant
memory shared little surface wording with the query. This is deliberately a
hard semantic case, not a production-quality benchmark; normal goal/location/
behavior tags reduce the gap. The corresponding micro-run took about 0.005 s
for lexical ranking and 0.31 s for ten BGE queries including model setup/cache
work. BGE therefore stays enabled for decision-boundary retrieval, Reflection
deduplication, and explicit recall, but it never runs as part of an otherwise
idle world-minute update.

The default decision view retrieves only a small split of short-term and
long-term records using the current goal, location, drives, and executable
behavior descriptions. Knowledge facts use the same task-conditioned ranker
but remain a separate section from subjective memories. A `recall_memory`
tool can request another bounded slice. Access count helps stable tie-breaking;
durable memories do not decay merely because time passes.

Reflection is the consolidation boundary. A completed action can create a
knowledge fact or event immediately when the configured result is observable;
it does not automatically create a durable experience. Reflection may submit
one concise lesson when the outcome contains a reusable change. Platform
checks evidence, length, provenance, and semantic duplication. Highly similar
lessons are rejected rather than blindly merged; a later Reflection can submit
a better summary that replaces or supersedes them.

The closure requirement is: action result → event or fact → bounded retrieval
in a later context → improved choice or a justified wait. The audit must show
the fact text, source, confidence, tier, and retrieved query, rather than only
showing that a database row exists.

