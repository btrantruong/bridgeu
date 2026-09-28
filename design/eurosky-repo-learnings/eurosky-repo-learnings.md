# bridgeu repo learnings — architecture, feature inventory, and the ranker seam

**Date:** 2026-09-02
**Scope:** read-only study of the whole repo at commit `576a1bb` (branch `baseline-pipeline`).
**Purpose:** an agent-facing reference for the next task — modifying the ranker.
**Start here** if the vocabulary is unfamiliar: §0 defines every term the rest of the document leans on.

---

## 0. Vocabulary

Definitions as this repo uses the words, not as the wider literature might. Where a term is
contested (**viewpoint** above all), the entry says what is actually computed today and what
that does *not* license you to claim.

### 0.1 The chain, in one line

```
firehose ──> event ──> slice ──> corpus (posts)  ──┐
                              └─> universe (users) ─┴─> features ──> store ──> candidates ──> feed
```

Each arrow narrows or derives; each output is an immutable named file. Reading the chain
backwards answers "where did this number come from".

### 0.2 Data-level terms

**Event.** One `commit`/`create` message from the firehose, projected down to eight fields
(`time_us, did, collection, uri, subject_uri, subject_author, created_at, text`). The atom of
the whole pipeline, and **identity-preserving**: a like arrives as the pair *(who, what)*, not
as an incremented counter. Three kinds survive ingestion — a post, a like, a repost — and a
post that carries `subject_uri` is a **reply** to that parent. What is *not* an event here:
deletes, un-likes, follows, blocks (filtered out or unparsed), and quote posts, which arrive
but lose their target. Counts are *derived* from events; events are never derived from counts.

**Slice.** A bounded replay of the firehose between two cursors — `raw/events/<name>.jsonl`.
A slice is a **time window over the whole network**, not a selection of content: it contains
whatever happened in those minutes. Append-only, never rewritten, the only thing besides the
pinned selections that is worth saving.

**Corpus.** A **pinned selection of posts** over a slice: a query result over raw, frozen and
given a name. `corpus-from-slice` selects the posts that were both *created and liked* inside
the window and keeps the top *n* by in-window likes. A corpus is the **post universe** — every
post-keyed feature, score and eval refers to exactly this set, so "the corpus" is the answer to
"which posts are we ranking?" Reproducibility is *same corpus file + same model versions*.

**User universe.** The paired selection of **users**, written in the same run to
`users/<stem>`: everyone who liked or reposted a corpus post, plus the corpus authors. It is
the user-side analogue of the corpus, and the pairing rule is one sentence: *post-keyed
artifacts refer to the corpus, user-keyed artifacts refer to the user universe, and one stem
names both.* When something is described as "computed over the universe", this is the set.

**Stem.** The shared name segment (`probe-200`) that versions both selections and every
artifact derived from them. Changing the selection changes the stem, which changes every
downstream filename — that is the whole versioning mechanism for selections.

**Key.** What a feature row is addressed by: an `at://` post URI for a post-keyed feature, a
`did:` for a user-keyed one. `KEYED_BY` on the extractor declares which, and the store's
`feature_values.key` column holds both kinds side by side.

**Catalog ref.** A name for an artifact (`corpus/probe-200`) that resolves to a path
(`data/corpus/probe-200.jsonl`). Steps depend on refs, never on each other's code.

**Artifact.** An immutable output file with a declared shape. "Immutable" is load-bearing: a
new selection or a new meaning produces a *new file*, never an edit to an old one, so a
consumer that pinned last week's artifact still sees last week's numbers.

**Derived and disposable.** Everything downstream of `raw/` and the pinned selections —
features, scores, the serving store — is regenerable from raw plus code, and is treated as
cache, not as a record.

### 0.3 Feature-level terms

**Feature.** A materialized, versioned, corpus-global function from keys to values, with one
owner module, a pure `compute(...)`, and five declarations (`FEATURE`, `MODEL_VERSION`,
`KEYED_BY`, `DEFAULT`, `REQUEST_SAFE`). "Corpus-global" is the reason it is materialized at
all: a percentile or an SVD needs the whole selection at once and cannot be computed for one
post in isolation.

**Feature artifact.** One JSONL file per (feature, stem, model version): a manifest line, then
`{"key", "value"}` rows.

**`MODEL_VERSION`.** A *meaning* version, not a build number. Change what the numbers mean →
bump it → new filename → pinned consumers are untouched.

**`DEFAULT` / lookup-with-default.** The declared value returned for a key the feature does not
have, so no consumer ever raises on a cold post or an unseen user. Choosing it is a modelling
decision, not a formality: `popularity` returns the **median** percentile for an unknown post
("we don't know"), where zero would assert "provably unpopular".

**`REQUEST_SAFE`.** A declaration that a feature is cheap enough to compute inline on a miss.
Currently `False` everywhere and read by nothing — an interface waiting for a use.

**Tier-1 / tier-2.** Tier 1 is cheap and record-local, extracted inline and never persisted;
tier 2 is corpus-global and materialized as a versioned artifact. (The research agenda uses a
three-tier scheme with different boundaries — see §8.2; the code's two-tier usage is the
operative one.)

**Embedding / latent space.** A map from keys to vectors where geometry, not the coordinates,
carries the meaning. `likes_spectral` puts posts and users in **one shared space**, which is
what makes `dot(user_vec, post_vec)` a **collaborative-filtering affinity**: "how much does
this user's revealed taste point at this post". Coordinates individually mean nothing; the
basis is defined only up to rotation within a degenerate subspace, and the sign of any axis is
arbitrary unless it is pinned deliberately.

**Degree.** How many edges a node has — for a user, how many likes they cast in the window;
for a post, how many it received. It is the precision of everything estimated about that node:
a user with one like has a vector that says little more than "liked that one post".

### 0.4 Viewpoint, and what it is not

**Viewpoint.** In a viewpoint model, a *viewpoint* is a **position in a latent space where
distance corresponds to disagreement**, inferred from *behaviour* — who engages with what —
rather than from text, self-description, or any declared stance. Two users are close because
the same content appeals to both, and far apart because it does not. The construct is
relational: it has no units, no natural origin, no intrinsic sign, and no meaning for a single
user in isolation. Only relative positions are interpretable.

**What exists in this repo today.** No feature named `viewpoint`. The closest thing is
`likes_spectral_users`: a 16-dimensional vector per did from the spectral decomposition of the
user × post like graph. The research agenda names "spectral embedding of the follow/interaction
graph" as *viewpoint model v1*, so these user vectors are best read as a **proxy for viewpoint,
not a viewpoint model** — they encode co-like structure, which mixes ideology with topic,
language, time zone, community, and simple popularity.

**Five properties that constrain any use of it:**

1. **Sign and rotation are arbitrary.** Any scalar "viewpoint axis" derived from these vectors
   must be sign-pinned by an explicit deterministic rule, or the same data will produce
   opposite-signed scores across runs.
2. **It is an estimate with per-user precision.** On the profiled slice, 80.0% of the 2,560 users who
   liked a corpus post cast exactly one such like — for most users the "viewpoint" is barely identified. Precision
   scales with degree, so any aggregate over engagers should weight or threshold on it.
3. **It is fitted on likes and usually scored against likes.** Measuring a post's audience
   diversity with vectors fitted on the very edges that form that audience is circular, and
   needs sample-splitting (or a different modality, such as follows) to be meaningful.
4. **Coverage is partial by construction.** Only the largest connected component of the like
   graph is embedded; everyone else is absent from the artifact.
5. **The zero vector means "no information", not "centrist".** An unknown user's every dot
   product is 0, which in a stable sort means *leave the order alone* — a deliberate fallback,
   not a claim that the user sits in the middle.

**Neighbouring terms it is easy to conflate:**

- **Post embedding** — the item-side vector from the same decomposition, in the same space.
  A post's position is not a viewpoint; it is where the post sits relative to the users who
  like it.
- **Audience diversity** — a property of a *post*, computed from the viewpoints of the users
  who engaged with it (spread, balance, or cross-group coverage). It needs a viewpoint model as
  an input and is not itself one.
- **Author diversity** — what `evals.metrics.diversity` measures today: entropy over the
  *authors* of the ranked posts. Unrelated to audiences, despite the shared word.
- **Bridging** — a ranking *objective*: reward content that earns positive response **across**
  people who usually disagree. It presupposes a way to say who disagrees with whom, which is
  why the viewpoint model comes first.
- **Elicited-response models (HaR / AR)** — predictions *from a post's text* about the
  responses it will attract: HaR = likelihood of attracting toxic replies, AR = affective
  intensity of responses. They say nothing about viewpoints, and the versions in
  `baseline/scorers.py` are deterministic heuristics standing in for fine-tuned models, matched
  only in output range.

### 0.5 Ranking and serving terms

**Algo.** A named ranking unit. Its name is also the **rkey** — the last segment of the feed
`at://` URI — which is how a request selects it. Publishing a new variant is a publish, not a
deployment.

**Ranker / scorer / rerank.** A *ranker* turns a candidate set into an ordered list. A *scorer*
is a function producing one component of that order (popularity, HaR bin, CF affinity). A
*rerank* reorders an existing candidate list rather than building one — `cf` is the only rerank
in the system, and it is applied at request time, per viewer.

**Candidate / candidate generation / working set.** A *candidate* is a post eligible to be
ranked for this request, in the shape `{post_id, author, created_at, text, features{...}}`.
*Candidate generation* is how that set is produced: either a published feed, or a query over
the feature tables ordered by stored popularity percentile. The *working set* is the top 500
(`CANDIDATES`) that one request ranks over.

**Feed doc.** An algo's batch output: `{algo, version, corpus, viewer, feed:[{post_id, score}]}`,
best-first, where `version` is `<corpus-stem>@<sha256[:8]>` of the corpus file — the thread
that lines up feed publishes, MLflow runs and eval reports.

**Published feed / feed version.** A feed doc loaded into the serving store as a new immutable
version. Pagination **cursors pin the version they started on** (`"<version>:<offset>"`), so a
re-publish never shuffles a viewer mid-scroll.

**Feed skeleton.** What the endpoint returns on the wire: bare `at://` URIs plus an optional
cursor. Scores are dropped; the AppView re-hydrates the posts for display. "The pipeline is a
machine for ordering URIs."

**Viewer.** The DID a request is personalized for — from the request JWT's `iss` claim in
production, or the `?viewer=` override in dev. The only place identity enters the serving path.

**Batch plane / serving plane.** Batch is expensive and viewer-agnostic, exchanges file
artifacts, and *fits* models. Serving is cheap and viewer-conditioned, reads a compacted store,
and only *applies* them. The store is the handoff point, and the `cf` rerank is the single
deliberate carve-out where arithmetic happens at request time.

**Engagement / percentile.** *Engagement* here is the specific weighted sum
`1.0·likes + 2.0·reposts + 1.5·replies` (weights owned by the `popularity` extractor).
*Percentile* is that sum's rank within the corpus, on [0, 1], with ties sharing the mean of
their positions — a scale that survives corpus growth, which is why it, not the raw sum, drives
candidate generation.

---

## 1. The approved plan

> **Step 1 — Full read pass.** In dependency order, not file order:
> 1. Contracts first: `catalog.py`, `store.py`, `features/artifacts.py`, `publish.py` — how artifacts are named, written, indexed.
> 2. Ingest: `jetstream.py`, `slice_corpus.py`, `corpus.py` — event row schema, corpus/user-universe pinning.
> 3. Features: `popularity.py`, `likes_spectral.py`.
> 4. Ranker path: `train.py`, `scorers.py`, `rank.py`, `serve.py`, `feed.py`.
> 5. Docs: `tutorial-new-feature-and-ranker.md`, `baseline-pipeline.md`, `research-agenda.md`.
> 6. Tests as ground truth for schemas (tests encode the contracts).
>
> **Step 2 — Write learnings doc.** Sections: architecture map; feature inventory table; feature construction detail; storage & retrieval; ranker seam; open questions.
>
> **Step 3 — Verify claims, don't trust prose.** Where doc and code disagree, code wins; record the discrepancy. Run `make test` once to confirm baseline green, so recorded schemas are the working ones.
>
> **Not doing:** no code changes, no pipeline runs against the live firehose, no ranker modification yet.

Executed as approved. Step 3 found the baseline **not** green — see §8. On a follow-up
instruction ("fix the blockers of make test", 2026-09-02) the failure in §8.1 was fixed and
covered by regression tests; §10 records that change.

## 2. Files produced by this task

| file | what it is |
|---|---|
| `2026-09-02-repo-learnings.md` | this document |
| `scripts/repro_spectral_matvec.py` | standalone reproduction of the `likes_spectral` ARPACK-path bug (§8.1), plus a validated candidate fix; prints the broken operator's output shape and compares the fixed operator's singular values against a dense reference |

The study pass itself modified no repository code. The follow-up fix (§10) changed
`packages/data-engineering/src/data_engineering/features/likes_spectral.py` and
`packages/data-engineering/tests/test_likes_spectral.py`.

---

## 3. Architecture map

Four Python workspace members under `packages/` plus `orchestration/`, one `uv` venv, `pnpm` workspace declared but currently holding no TS members.

```
packages/data-engineering   the data layer: catalog, store, tracking, ingest, features
packages/algo-interface     the SERVE contract: feed-doc helpers + the FastAPI endpoint
packages/baseline           the training chain (baseline-train) + a pure ranking function
packages/evals              evaluate CLI + feed metrics
orchestration/              Dagster assets wrapping the same steps
```

**The organizing rule** (stated in [tutorial-new-feature-and-ranker.md:256-269](../docs/tutorial-new-feature-and-ranker.md#L256-L269) and honoured everywhere in the code I read): *steps depend on each other's outputs, never on each other's code.* A step names an artifact by catalog ref; `data_engineering.catalog` resolves it to a path. Importing shared **library** code (`algo_interface.feed`, `artifacts.read_artifact`, `popularity.WEIGHTS`) is explicitly allowed; importing another pipeline's internals is not.

### CLI entry points (from the `[project.scripts]` blocks)

| command | module | role |
|---|---|---|
| `jetstream-slice` | `data_engineering.ingest.jetstream:slice_main` | bounded firehose replay → raw events |
| `jetstream-tail` | `data_engineering.ingest.jetstream:tail_main` | live tail → micro-batch files |
| `corpus-from-slice` | `data_engineering.ingest.slice_corpus:main` | pin corpus **and** user universe |
| `fetch-corpus` | `data_engineering.ingest.fetch:main` | alternative corpus from the public AppView |
| `compute-popularity` | `data_engineering.features.popularity:main` | feature artifact |
| `compute-likes-spectral` | `data_engineering.features.likes_spectral:main` | two feature artifacts (one SVD) |
| `baseline-train` | `baseline.train:main` | the chain: compute → publish → index |
| `publish-serving` | `data_engineering.publish:main` | artifacts → serving store |
| `bridgeu-serve` | `algo_interface.serve:main` | `getFeedSkeleton` endpoint |
| `evaluate` | `evals.cli:main` | feed artifact + corpus → metrics report |

There is **no feed-producer CLI right now.** The `baseline` batch CLI was retired (commit `e117143`); evals is meant to own feed generation. `packages/baseline/src/baseline/rank.py` still exists and is fully tested, but **nothing calls it** — no entry point, no import outside its own tests. This matters a lot for the ranker task (§7).

### Data zones under `DATA_ROOT` (default `./data`, env `BRIDGEU_DATA_ROOT`)

```
raw/events/<slice>.jsonl                          raw event rows
corpus/<stem>.jsonl                               pinned post selection
users/<stem>.jsonl                                paired user universe
features/<feature>/<stem>.<model_version>.jsonl   feature artifacts
scores/<algo>/<stem>.jsonl                        feed docs (nothing writes these today)
serving/store.db                                  sqlite serving store (dev)
mlflow.db, mlartifacts/                           MLflow ledger
```

`catalog.resolve` rules ([catalog.py:39-47](../packages/data-engineering/src/data_engineering/catalog.py#L39-L47)): an existing filesystem path passes through untouched; otherwise the ref is joined under `DATA_ROOT` and gets `.jsonl` appended when it has no suffix **or** when the suffix matches `^\.v\d+$` (so `probe-200.v1` → `probe-200.v1.jsonl`, not `probe-200.jsonl`).

---

## 4. Feature inventory

Three published feature tables, produced by **two** owner modules. This is the complete roster as of this commit.

| feature | keyed by | owner module | `MODEL_VERSION` | value schema | `DEFAULT` | `REQUEST_SAFE` |
|---|---|---|---|---|---|---|
| `popularity` | post (`at://` uri) | [features/popularity.py](../packages/data-engineering/src/data_engineering/features/popularity.py) | `v1` | `{likes:int, reposts:int, replies:int, engagement:float, percentile:float}` | `{likes:0, reposts:0, replies:0, engagement:0.0, percentile:0.5}` | `False` |
| `likes_spectral` | post (`at://` uri) | [features/likes_spectral.py](../packages/data-engineering/src/data_engineering/features/likes_spectral.py) | `v1` | `{vector: float[dim], n_likes:int}` | `{vector:[0.0]*dim, n_likes:0}` | `False` |
| `likes_spectral_users` | user (`did:`) | same module (`USER_FEATURE`) | `v1` | `{vector: float[dim], n_likes:int}` | `{vector:[0.0]*dim, n_likes:0}` | `False` |

The extractor contract is five module-level declarations plus a pure `compute(...)`: `FEATURE`, `MODEL_VERSION`, `KEYED_BY`, `DEFAULT`, `REQUEST_SAFE`. `likes_spectral` is the one module that owns **two** feature names, because both artifacts come out of a single decomposition.

Notes that matter when adding a feature:

- `DEFAULT` is what makes every consumer total — a missing key never raises. It is stored in `feature_sets.default_value` at publish time and returned by `Store.lookup`. Choose it deliberately: `popularity` uses the **median** percentile (0.5) for "unknown", not 0, which would assert "provably unpopular".
- `MODEL_VERSION` is a *meaning* version. Changing a formula means bumping it, which changes the artifact filename, which leaves pinned consumers untouched.
- `REQUEST_SAFE` is `False` on all three. It is a **declaration only** — nothing in the codebase reads the flag. There is no inline-compute-on-miss machinery; the request path uses defaults instead.
- `DEFAULT_DIM = 16` for the spectral vectors, but `build_manifest(stem, dim)` writes the *actual* dim into the manifest default, so a `--dim 8` run publishes an 8-long zero vector.

### 4.1 How `popularity` is constructed

[popularity.py:44-72](../packages/data-engineering/src/data_engineering/features/popularity.py#L44-L72). Input is the **raw corpus records**, not events.

1. Pull three counts per post from the corpus record: `like_count → likes`, `repost_count → reposts`, `reply_count → replies`.
2. `engagement = 1.0·likes + 2.0·reposts + 1.5·replies`, weights in `WEIGHTS` — the single source of truth, imported by `baseline.rank`.
3. `percentile` = average-rank empirical CDF: sort all engagement scores ascending, give each distinct value the **mean of the 1-based positions** it occupies, divide by `n`. Ties share a percentile; the maximum value gets exactly 1.0 when unique.
4. Rows emitted most-popular-first.

Plain dict arithmetic, no dataframe — deliberate, per the module docstring, at corpus scale (10²–10⁴ rows).

Because the counts come from the corpus record and `corpus-from-slice` derives those counts from in-window events only, `popularity` on a slice-pinned corpus is **in-window engagement**, not lifetime engagement — `reply_count` and `quote_count` are hard 0 there ([slice_corpus.py:71-72](../packages/data-engineering/src/data_engineering/ingest/slice_corpus.py#L71-L72)). So on a slice corpus, `engagement = likes + 2·reposts` in practice and the `replies` weight never fires. On a `fetch-corpus` corpus the counts are lifetime AppView counts and all three fire.

### 4.2 How `likes_spectral` / `likes_spectral_users` are constructed

[likes_spectral.py:175-243](../packages/data-engineering/src/data_engineering/features/likes_spectral.py#L175-L243). Input is **raw like events**, optionally restricted to a corpus's post URIs.

1. `like_pairs` — filter to `app.bsky.feed.like` with non-null `did`/`subject_uri`, project to those two columns, `.unique()` (one user–post edge counted once).
2. Optional filter `subject_uri ∈ post_uris` — applied **before** the SVD, so the geometry is the corpus's own like structure.
3. Build binary sparse `A` (users × posts) via `np.unique(..., return_inverse=True)` for both axes.
4. **Restrict to the largest connected component** of the bipartite graph (`largest_component`, via `scipy.sparse.csgraph.connected_components` over the symmetric block matrix). Keys outside it are simply *absent from the artifact* and therefore get the zero-vector default. This is why embedding coverage is smaller than the corpus.
5. Degree-normalize: `Â = D_u^{-1/2} A D_p^{-1/2}`.
6. **Deflate the analytically-known trivial triplet** `σ₀=1, u₀=√(d_u/L), v₀=√(d_p/L)` where `L` is total likes — subtract `u₀v₀ᵀ`, rather than discarding the first singular vector.
7. Truncated SVD of `Â − u₀v₀ᵀ` at `k = min(dim, min(shape))`. ARPACK `svds` over a matrix-free `LinearOperator` when `k < min(shape)`; a dense `np.linalg.svd` on the materialized deflated matrix otherwise.
8. Un-normalize back to node space: `post_emb = D_p^{-1/2} V Σ`, `user_emb = D_u^{-1/2} U Σ`. **One shared latent space**, which is what makes `dot(user_vec, post_vec)` a CF affinity.
9. Zero-pad to `dim` if the graph supported fewer components; round each coordinate to 6 decimals; emit rows highest-degree-first with `n_likes` = that node's degree **within the retained component**.

The two design choices the module argues for explicitly, both about the σ=1 degeneracy: σ=1 has multiplicity equal to the number of connected components and those singular vectors are constant on each component, so (a) embedding a disconnected graph returns component labels instead of taste, hence step 4, and (b) "drop the first singular vector" would discard an arbitrary direction of a degenerate subspace, hence the exact deflation in step 6.

**Step 7's ARPACK branch is currently broken — see §8.1.** Every corpus-scale run takes that branch.

---

## 5. Storage and retrieval

### 5.1 The file artifact

One JSONL per `(feature, stem, model_version)` at `features/<feature>/<stem>.<model_version>.jsonl`. Line 1 is the manifest, every later line a keyed row ([features/artifacts.py](../packages/data-engineering/src/data_engineering/features/artifacts.py)):

```json
{"meta": {"feature", "model_version", "corpus", "keyed_by", "computed_at", "default"}}
{"key": "at://... | did:...", "value": <scalar | vector | object>}
```

`write_artifact` streams (any iterable); `read_artifact` returns `(meta, {key: value})` for batch consumers.

### 5.2 The serving store

[store.py](../packages/data-engineering/src/data_engineering/store.py). Backend is an env knob, not a code path: `BRIDGEU_DB_URL=postgresql://...` → psycopg; unset → sqlite at `DATA_ROOT/serving/store.db`. One SQL dialect; `_execute` rewrites `?` → `%s` for Postgres.

Four tables:

| table | PK | holds |
|---|---|---|
| `feed_versions` | `(algo, version)` | one row per publish: label (the corpus-derived version string), corpus, viewer, `published_at` |
| `feed_items` | `(algo, version, rank)` | the ranked `post_id` + `score` |
| `feature_sets` | `(feature, model_version)` | the manifest: corpus, `keyed_by`, `computed_at`, `default_value` (JSON) |
| `feature_values` | `(feature, model_version, key)` | one JSON-encoded value per key |

Plus one index, created by `ensure_scorer_indexes()`: `ix_feature_values_key ON feature_values (key)`. The PK ordering already serves point lookups; this index serves the *fan-out* direction ("every feature for candidate X"), which is what `lookup_all_features` uses.

Retrieval API, and which of it is actually wired up today:

| method | semantics | used by |
|---|---|---|
| `lookup(feature, mv, keys)` | lookup-with-default; every requested key comes back | `cf_rerank` |
| `lookup_all_features(key)` | all `(feature@mv) → value` for one key; **no defaults**, missing rows absent | nothing yet — built for a future scorer fan-out |
| `top_by_feature(feature, mv, field, limit)` | candidate generation: order keys by a numeric field inside the JSON value | `serve.candidates` |
| `latest_feature_version(feature)` | newest `model_version` by `computed_at` | `serve` (both paths) |
| `feature_default(feature, mv)` | the declared default | `lookup` |
| `publish_feed / read_feed / latest_feed_version / latest_feed_info` | versioned feeds + version-pinned cursor paging | `serve`, `publish-serving` |

Two behaviours worth knowing before writing a ranker against this store:

- **`lookup` is one SQL round-trip per key** ([store.py:274-288](../packages/data-engineering/src/data_engineering/store.py#L274-L288)) — the loop issues a separate `SELECT` per key rather than an `IN (...)`. `cf_rerank` calls it with all 500 candidate post ids, so a `cf` request is ~501 queries. Correct, indexed, and O(n) round-trips; the obvious optimization if request latency becomes a concern.
- **`top_by_feature` interpolates the sort `field` into SQL**, guarded by the regex `^[A-Za-z_][A-Za-z0-9_]*$` because a JSON field can't be a bind parameter on both backends. Any new candidate-generation field must satisfy that shape.
- **Publishing a different corpus under the same `(feature, model_version)` deletes the previous rows** ([store.py:209-218](../packages/data-engineering/src/data_engineering/store.py#L209-L218)) — so the store always describes one selection. Publishing the *same* corpus upserts.

### 5.3 Provenance

Every artifact-producing step calls `tracking.log_step(step=..., params=..., metrics=..., artifacts=[...])`. `step` becomes the MLflow experiment name. Backend: sqlite at `DATA_ROOT/mlflow.db`, artifacts under `DATA_ROOT/mlartifacts/<step>/`. `BRIDGEU_TRACKING=off` disables it; `MLFLOW_TRACKING_URI` points at a shared server. Non-numeric metric values are silently dropped (params carry those).

---

## 6. The request path, end to end

[serve.py](../packages/algo-interface/src/algo_interface/serve.py), `GET /xrpc/app.bsky.feed.getFeedSkeleton`.

1. `algo = rkey` of the `feed` at:// URI, else `BRIDGEU_SERVE_ALGO` (default `baseline`).
2. `source_algo = default_algo() if algo == "cf" else algo` — **`cf` is not an algo, it is a rerank applied on top of the default algo's candidates.**
3. If a feed is published for `source_algo` **and** `algo != "cf"`: page it in SQL, cursor `"<version>:<offset>"` pinned to the version it started on.
4. Otherwise build candidates in Python: `top_by_feature("popularity", field="percentile", limit=500)` — constants `CANDIDATE_FEATURE`, `CANDIDATE_FIELD`, `CANDIDATES` at the top of the module. Empty → HTTP 404 with a "run baseline-train" hint.
5. If `algo == "cf"`: `cf_rerank` — viewer DID from `?viewer=` or the request JWT's `iss` claim (**payload decoded without signature verification**; production must verify), one `lookup` on `likes_spectral_users` for the viewer, one on `likes_spectral` for the candidates, `score = dot(viewer_vec, post_vec)`, stable descending sort. Unknown viewer → zero vector → all scores 0 → incoming order preserved. `zip(..., strict=True)` on the vectors deliberately raises if the user and post tables came from different training runs.
6. Slice `[offset:offset+limit]`, emit `{"feed": [{"post": uri}], "cursor"?}` — the score is dropped on the wire.

Note the asymmetry in step 3: a published feed under the rkey `cf` is ignored for candidate generation (the condition excludes `cf`), and instead `cf` reranks whatever the **default** algo offers.

---

## 7. The ranker seam — what to touch

There are three distinct places something called "ranking" happens. Knowing which one is meant is the first decision for the next task.

### A. Batch feed producer (the `scores/` artifact)

The contract exists and is stable, but **no producer is wired.** To add one, per the tutorial:

- New workspace package `packages/<algo>/` (uv globs `packages/*`), dependencies `algo-interface` + `data-engineering`, one `[project.scripts]` entry.
- Read corpus with `load_corpus(catalog.require(ref))`, read features by ref with `read_artifact`, **check `meta["model_version"]` and refuse a mismatch**, look up with `table.get(key, meta["default"])`.
- Emit with `feed_doc(algo, corpus_ref, viewer, feed, corpus_version(corpus_path))` then `emit(doc, out_ref, params=...)`. The version string is `<corpus-stem>@<sha256[:8] of the corpus file>` — the thread that lines up feed publishes, MLflow runs, and eval reports.
- `publish-serving --feed <ref>` loads it; `bridgeu-serve` then serves it under the algo name as rkey, no serving code needed in the package.

`packages/baseline/src/baseline/rank.py` is a ready-made example of a pure ranking function against the candidate shape `{post_id, author, created_at, text, features:{likes, reposts, replies}}`: HaR bin ≥ 2 demoted below everything (bins from `bisect(HAR_BOUNDARIES, har_score)`, boundaries `[0.557, 0.572, 0.581, 0.6]`), then `har_bin` ascending, `popularity` descending, `ar` descending; demoted bucket ordered by `har_bin` then `ar`. Score returned is the rank weight `n - i`, not a calibrated scalar, because the order is lexicographic over components. `baseline.ingest.to_candidate` maps a raw corpus record into that shape. The HaR/AR scorers in `scorers.py` are **deterministic keyword/punctuation heuristics standing in for Dante's fine-tuned transformers**, calibrated only to the same output ranges — they are not a model.

### B. Query-built candidate order (no feed published)

Change what `serve.candidates` orders by: `CANDIDATE_FEATURE` / `CANDIDATE_FIELD` at [serve.py:52-53](../packages/algo-interface/src/algo_interface/serve.py#L52-L53). Any new feature whose value JSON carries a numeric field of a safe name can drive the candidate set with no other change. This is the cheapest ranking intervention in the repo.

### C. Request-time rerank (the `cf` carve-out)

`cf_rerank` at [serve.py:101-126](../packages/algo-interface/src/algo_interface/serve.py#L101-L126), dispatched by the `CF_RKEY = "cf"` constant. A new per-viewer rerank means a new rkey branch in `get_feed_skeleton` plus a rerank function doing `REQUEST_SAFE` arithmetic over `Store.lookup`. The expensive fit stays in batch; the request path applies it.

### D. The training chain

`baseline.train.train()` is a linear script, not a plugin system: it hardcodes the two extractors, writes three artifacts, publishes them and indexes. Adding a feature to the trained set means editing that function (and, for Dagster parity, adding an asset in `orchestration/src/orchestration/assets.py` and to the `training_pipeline` selection).

---

## 8. Findings from verification

### 8.1 `make test` is not green — the spectral ARPACK path is broken

`uv run pytest -q` → **3 failed, 65 passed**. All three failures are in `packages/data-engineering/tests/test_likes_spectral.py`:

```
test_co_liked_posts_embed_closer_than_disjoint
test_only_the_largest_component_is_embedded
test_user_embeddings_share_the_post_space
ValueError: cannot reshape array of size 16 into shape (4,1)
```

Root cause, confirmed by `scripts/repro_spectral_matvec.py`: the deflated operator in [likes_spectral.py:118-123](../packages/data-engineering/src/data_engineering/features/likes_spectral.py#L118-L123) is written for 1-D input only —

```python
matvec=lambda x: A_hat @ x - u0 * (v0 @ x),
```

scipy's `LinearOperator._matmat` feeds `matvec` a **column vector** `col.reshape(-1, 1)`. With `x` of shape `(n_posts, 1)`: `A_hat @ x` is `(n_users, 1)`, but `v0 @ x` is `(1,)` and `u0 * (v0 @ x)` broadcasts to `(n_users,)`, so the subtraction broadcasts to `(n_users, n_users)`. scipy then fails to reshape it back to a column. The repro prints `(6, 6)` where `(6, 1)` was required.

Reach: the dense branch (`k >= min(A_hat.shape)`) is fine, which is why 5 of the 8 spectral tests and the whole `baseline-train` test pass — those graphs are tiny. **Every corpus-scale run takes the ARPACK branch.** Confirmed on a synthetic 50 users × 30 posts graph at `dim=4`:

```
FAILS on ARPACK path: ValueError cannot reshape array of size 2500 into shape (50,1)
```

So `baseline-train` on any real slice currently fails at step 2, and the CF features cannot be produced. This is a blocker for anything that consumes `likes_spectral*` on real data.

Whether this is a regression from a scipy upgrade (installed: scipy 1.17.1, numpy 2.4.6) or was never exercised at size, I did not determine; git history would say. **Fixed in §10.**

### 8.2 Documentation drift (code wins in each case)

- **`docs/operations.md` is stale relative to the rest.** It describes a `pipeline` CLI and `uv run <algo>` serving flags that don't exist, raw zones `raw/posts/` and `raw/edges/` (actual: `raw/events/`), and states "No database in the batch path" / "Postgres/Redis only when serving needs writes" while `data_engineering.store` is now a published serving store read by `bridgeu-serve`. It also cross-references `features.md`, `decisions.md`, and `docs/algos.md` — **none of these files exist** (`algo_interface/__init__.py` and `baseline/__init__.py` reference `docs/algos.md` too).
- **Tier numbering conflicts.** `docs/planning/research-agenda.md` defines a *three*-tier scheme (tier 2 = corpus-global non-model, tier 3 = corpus-global model-backed). Every code docstring and `docs/pipelines/baseline-pipeline.md` use a *two*-tier scheme with "tier 2" meaning the materialized, versioned artifact. Under the research-agenda scheme `likes_spectral` would be tier 3. The code's usage is the operative one.
- **`REQUEST_SAFE` is declared but never read** by any consumer.
- `docs/pipelines/baseline-pipeline.md` and the tutorial are accurate against the code; I found no substantive contradiction in either.

### 8.3 Smaller observations

- `packages/baseline/src/baseline/rank.py` and `scorers.py` are live-tested but unreachable from any entry point — dead code with a purpose, waiting on the evals-owned feed producer.
- `evals` has one metrics module (`diversity.py`): `k`, `resolved`, `unique_authors`, `author_entropy_bits`, `max_author_share`, `mean_likes`, `corpus_coverage`. No viewpoint/bridging metric exists yet; the research agenda lists them as R1/R2 work.
- The Dagster graph covers ingestion → features → `serving_store` only. Feed and eval assets left with the retired `baseline` CLI.
- `corpus_from_events` skips any post with zero in-window likes ("no CF signal"), so a corpus is always ≤ n and always has like edges.
- `users_from_events` counts interactions **against the corpus**, not across the whole slice, and includes corpus authors with zero counts.
- `parse_event` keeps only `commit`/`create`; deletes and identity events are dropped at the door, so there is no un-like or deletion signal anywhere downstream.

---

## 9. Open questions for the ranker task

1. **Which seam?** A new batch feed producer (§7A), a different candidate-generation feature (§7B), or a new request-time rerank rkey (§7C). These have very different blast radii.
2. ~~**Is §8.1 in scope?**~~ Resolved: fixed in §10, `make check` green. `likes_spectral*` is available on real data again — though it has still never been *run* on a real slice here, only on synthetic graphs.
3. **Does the ranker need a new feature, or only a new ordering over existing ones?** Only three feature tables exist, and two of them are the same 16-d vector on two key types.
4. **Where does the feed producer live** — a new `packages/<algo>/`, or inside `evals` as the docs say it eventually will? Committing to the wrong one costs a move later.
5. **Per-viewer or not?** Batch feed docs carry a single `viewer` field; anything genuinely per-viewer belongs on the request path or in the evals-owned generator that builds feeds per user.

---

## 10. Fix applied — the deflated-SVD operator (2026-09-02, follow-up)

Blocker §8.1 fixed. `make check` is green: **70 passed**, `black --check` and `ruff` clean.

### The change

[`features/likes_spectral.py`](../packages/data-engineering/src/data_engineering/features/likes_spectral.py) — the inline lambdas inside `_truncated_svd` are replaced by a named `_deflated_operator(A_hat, u0, v0)` that builds the same matrix-free `Â - u₀v₀ᵀ`, but correctly for every operand shape scipy passes:

```python
def applier(M, left, right):
    def apply(x):
        x = np.asarray(x)
        block = x.reshape(x.shape[0], -1)
        out = M @ block - np.outer(left, right @ block)
        return out.ravel() if x.ndim == 1 else out
    return apply
```

and registers `forward`/`adjoint` as **all four** of `matvec`, `rmatvec`, `matmat`, `rmatmat`. `_truncated_svd` keeps its dense branch and its descending re-sort unchanged; only the operator construction moved out.

Two properties make it correct where the old form was not: reshaping the operand to `(n, -1)` means the rank-1 correction is always a 2-D outer product (`np.outer(left, right @ block)`), never a broadcast between a `(n,)` column and a `(k,)` row; and `ravel()` restores the 1-D contract only when the caller passed 1-D.

### Options considered

| option | why not / why |
|---|---|
| **Guard the operand: `x.ravel()` inside the existing lambdas** | Restores 1-D semantics but returns 1-D where scipy expects a column, so `_matmat`'s `np.hstack` builds the wrong shape. Fixes the symptom, not the contract. |
| **Drop the LinearOperator; densify and use `np.linalg.svd` always** | Correct and simple, but discards the entire reason the sparse path exists — the module's docstring argues 10⁴–10⁵ nodes make a dense matrix gigabytes. Rejected on the stated design intent. |
| **Deflate by explicitly subtracting the trivial component from `A_hat` first** | Would materialize a dense rank-1 update into a sparse matrix, destroying sparsity. Same objection. |
| **Matmat-aware operator (chosen)** | Keeps the matrix-free deflation and the sparsity argument intact, and additionally lets ARPACK apply whole blocks instead of falling back to per-column `matvec` — the `matmat`/`rmatmat` registration is a small throughput win on top of the correctness fix. |

### Regression coverage added

Two tests in [`tests/test_likes_spectral.py`](../packages/data-engineering/tests/test_likes_spectral.py). The bug survived because *every existing fixture was small enough to take the dense branch* — so the new tests target the branch itself, not just the outcome.

- `test_deflated_operator_matches_the_dense_matrix` — asserts the operator reproduces the dense `Â - u₀v₀ᵀ` for 1-D input, a 2-D block, a column vector, and the adjoint. Verified to discriminate: against the pre-fix operator, the 1-D case passes and the other three raise `ValueError: cannot reshape array of size 16 into shape (4,1)`.
- `test_truncated_path_recovers_communities` — a 25-user × 12-post two-community graph at `dim=4`, so `k < min(shape)` forces the ARPACK branch. Asserts nothing is dropped (one connected component) and that each user's summed affinity to their own community's posts exceeds their affinity to the other's — the CF property the `cf` rerank depends on.

### Verification beyond the suite

On a synthetic 50-user × 30-post graph at `dim=4` (the size that failed before the fix), the ARPACK triplets were compared against a dense reference of the same deflated matrix:

```
arpack sigma: [0.64870231 0.61717665 0.59568267 0.58825258]
dense  sigma: [0.64870231 0.61717665 0.59568267 0.58825258]
matmat residual: 2.2e-16   rmatmat residual: 3.3e-16   1-D matvec residual: 1.1e-16
```

So the truncated path now returns the same decomposition the dense path would, to machine precision. `scripts/repro_spectral_matvec.py` still reproduces the original defect (it constructs the pre-fix operator inline) and remains runnable as a demonstration.

**Not verified:** no run against a live firehose slice. The fix is exercised on synthetic graphs and the existing test corpora only.
