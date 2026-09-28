# Implementation note — how the DS pipeline, the Eurosky baseline repo, and MySky's API run together

**Date:** 2026-09-28
**Reads with:** `design-brief.md` (interfaces), `wps-revised.md` (who builds what),
`api-learnings.md` (MySky facts), `../eurosky-repo-learnings/eurosky-repo-learnings.md`
(Eurosky baseline repo facts), `../concept-paper/overview.md` and
`../../archive/2026-09-03-foryou-pipeline/2026-09-03-foryou-pipeline-design.md` (DS pipeline).

---

## 1. Three codebases, three roles

| codebase | what it is | role in production BridgEU |
|---|---|---|
| **DS pipeline** (`concept/`) | the specification: O1 communities, O2 like-graph representation, O3 diverse approval, O4 response quality, retrieval R1–R4, weighted ranking with a harm gate, evaluation gates G1–G5 | **defines** every signal and its null/default; owns estimator code and parameters |
| **Eurosky baseline repo** (`./eurosky`, engineer-built) | Python `uv` workspace: artifact catalog, immutable versioned feature artifacts (`FEATURE`, `MODEL_VERSION`, `KEYED_BY`, `DEFAULT`, pure `compute`), serving store, MLflow tracking, Dagster orchestration, evals, Jetstream ingest, a dev `getFeedSkeleton` | **batch plane**: runs the DS estimators on a schedule, tracks runs, validates and promotes artifacts. Its own serving endpoint becomes the offline/dev harness, not production |
| **MySky API** (`./api`, GreenEarth) | production feed generator on Cloud Run: generator/ranker plugins, request path, Firestore state, interaction logging, PostHog, monitoring, deploy/rollback | **serving plane** and **measurement source**: applies the signals per request; records what was served and seen |

The dividing rule, carried over from the Eurosky repo and from MySky's own design: **batch fits,
serving applies.** Anything that needs the whole corpus (community detection, a null model over
all posts, a CF decomposition) runs in batch; the request path only looks up precomputed values.

---

## 2. Architecture

```
 CAPTURE (public events)          BATCH PLANE (Eurosky infra, eurosky repo)        SERVING PLANE (MySky, ./api)
 ───────────────────────          ──────────────────────────────────────────       ─────────────────────────────
 Jetstream follows ──► follow-event log ─┐
 follow snapshot (DS, S3) ───────────────┼─► [Dagster] graph@t ─► O1 communities ─► A1, A2 ──promote──┐
                                         │                                                           │
 ingex: likes, replies (ES) ──export──►  likes/replies window ─┬─► O2 CF vectors                      │
 (reposts: gap)                                                ├─► O3 diverse approval ─► A3, A4 ─────┤
                                                               └─► O4 response quality ─► A5 ─────────┤
                                                                                                     ▼
                                      MLflow run per artifact; schema validation;          ES aliases bridgeu_* / GCS pointer
                                      evals gates G1–G5 before promotion                              │
                                                                                                     ▼
 Bluesky AppView ──getFeedSkeleton──►  FeedConfig "bridgeu" ─► generators: two_tower, bridging_*, network_likes
                                                            ─► rankers: heavy_ranker, bridging, perspective (+gates)
                                                            ─► MMR ─► skeleton + signed feedContext
 Bluesky AppView ──sendInteractions─►  interactions (seen/like/…) ─┐
                                       feed snapshots (+ artifact versions, arm) ─┴─► durable export ─► DS evaluation
                                                                                                      (eurosky evals)
```

---

## 3. Where each DS block is implemented

| DS block | implemented in | reuses | new work |
|---|---|---|---|
| Follow-graph capture + rewind | per **[D1]** (ingex extension or Eurosky service) | ingex Jetstream follow consumer, or Eurosky's existing service; DS snapshot on S3 | append-only follow-event log; point-in-time reconstruction |
| O1 communities (A1, A2) | eurosky repo, Dagster asset | catalog, artifact contract, MLflow | detection + outgroup + banner + naming code (DS) |
| O2 behavioral vectors (batch only) | eurosky repo | `likes_spectral` (ARPACK fix already applied, §10 of the repo learnings) | run on production windows |
| O3 diverse approval (A3, A4) | eurosky repo | artifact contract | estimator with null model, sample-splitting, shrinkage (DS) |
| O4 response quality (A5) | eurosky repo (phase 3) | — | reply-tone estimator (DS) |
| R1 relevance retrieval | **MySky `two_tower`** | as-is | — |
| R2 bridging retrieval | MySky api | `popularity_cache` pattern | `bridging_global`, `bridging_community` |
| R3 in-network | **MySky `network_likes`, `followed_users`** | as-is | — |
| R4 exploration | MySky `random_posts` as a stand-in | as-is | a dedicated under-measured-fresh-post generator, later |
| Ranking | MySky `run_predict` | `heavy_ranker`, `perspective` | `bridging` ranker; E1 default policy; E2 gates |
| Diversification | MySky MMR | as-is | optional community penalty |
| Evaluation | eurosky `evals` + DS | exported snapshots and interactions | overlap, source survival, diverse-approval@k, cross-community exposure |

---

## 4. Implementation choices — options considered and the choice made

Each row is recorded because it changes what a downstream number means or who carries an
operational burden. "Choice" is the proposal in this note; items marked **(DS)** or **[D#]**
still need the owner's sign-off.

### 4.1 Where the feed is served

| option | considered | outcome |
|---|---|---|
| Eurosky repo's own `getFeedSkeleton` + serving store | has a working endpoint, versioned feeds, `cf` rerank | **not chosen for production**: no interaction logging, exclusions, auth verification (JWT signature not verified per repo learnings §6), monitoring or rollout controls; rebuilding them duplicates MySky |
| Eurosky-hosted generator calling MySky `/candidates/generate` + `/rank/predict` | works today with an API key | **fallback** if code contribution to `./api` is not possible; adds a cross-org hop per request |
| **Plugins inside MySky API** | narrow ABCs, registry, config-only feeds | **chosen**; see `design-brief.md` [D2] |

### 4.2 How signals cross from batch to serving

| option | considered | outcome |
|---|---|---|
| Write new fields into MySky's `posts` documents | no join at request time | **not chosen**: couples DS versions to ingex mappings; one version live at a time; re-indexing cost |
| Firestore documents per key | already used for caches | **not chosen for large tables**: per-document write cost for 10⁶–10⁷ rows per refresh; fine for per-community pools |
| BridgEU signals HTTP service on Eurosky | full DS control | **not chosen for the request path** (cross-org latency and availability dependency); acceptable for the visualization view |
| **ES indices behind aliases (large keyed tables) + GCS artifact with pointer (small tables)** | bulk load + atomic alias swap = versioning and rollback; GCS promotion tooling exists in `./api` | **chosen** |

### 4.3 Where batch jobs run

| option | outcome |
|---|---|
| Manual notebook runs | acceptable only for A1/A2 in phase 1 (the WPS already says "updated by hand periodically") |
| Cloud Run jobs in MySky's GCP | possible; puts DS research code in MySky's operational scope |
| **Dagster in the eurosky repo on Eurosky infra** | **chosen**: the repo already has assets, catalog refs, MLflow and the artifact contract; keeps estimator iteration with DS; European compute |

### 4.4 Source of likes/replies for batch

| option | outcome |
|---|---|
| **Scheduled export from ingex/ES to object storage** (partitioned by hour/day) | **chosen**: single capture, same corpus that serving hydrates from, no request-path load |
| Scoped read key and scroll/PIT over `likes` | fallback; loads the serving cluster |
| Independent Jetstream capture in the eurosky repo (`jetstream-tail`) | fallback if export is refused; also the way to get **reposts** if ingex does not add them; drift from MySky's corpus shows up as low hydrate-hit share in the bridging generators |

### 4.5 Representation used for behavioral distance (O3) vs for relevance (R1) **(DS)**

| option | outcome |
|---|---|
| Two-tower user embeddings for both | per-user inference over HTTP (`/embeddings/user`, sequential, 55 s deadline) does not scale to all engagers; content-mediated rather than co-like |
| **Spectral CF (`likes_spectral`) in batch for O3; MySky `two_tower` for R1** | **proposed**: batch-native, pure co-like structure as the concept paper specifies. Consequence to record: the relevance pathway and the bridging pathway use different representations, so overlap(relevance, bridging) measures what the *production* relevance retriever misses, which is the question §3 of the concept paper asks |

### 4.6 Granularity of bridging retrieval **[D3]**

Global pool (one list for all viewers) and per-community pool (posts approved by the viewer's
community and its outgroup) are both cache-shaped and cheap; per-user is not in v1. Both are built
as separate generators so their contributions are measured separately.

### 4.7 Score transform for the bridging ranker **(DS)**

MySky normalizes by declared theoretical bounds, not within-slate percentiles (the DS design §5.1
specified percentiles over |S| ≈ 500–1 000; MySky slates are ≤ 100–200). A bounded transform of
`z` (e.g. standard-normal CDF) fits the framework; a within-window percentile also does. Kept as
the parameter `score_transform` until DS decides.

---

## 5. Flows

### 5.1 Community refresh (manual, weeks)
1. Materialize graph at time `t` from snapshot + follow-event log.
2. Run O1 (resolution and other parameters logged to MLflow); compute outgroups, banners, names.
3. Gates: G1 (communities ≠ language partitions), label matching to the previous run.
4. Write A1/A2 with `model_version`, `run_id`, `inputs.graph_at=t`; validate schema.
5. Load A1 into a new ES index; flip alias `bridgeu_user_community`; publish A2 to GCS; flip pointer.
6. MySky picks up A2 at next deploy (pinned) or via a guarded reload; every snapshot item logs the
   versions it used.

### 5.2 Bridging refresh (every `bridging_refresh_minutes`)
1. Pull the latest like (and repost) window from the export.
2. O3 with sample-splitting (fit vectors on one fold/time slice, score dispersion on another),
   null model at matched reach, degree shrinkage, abstention below `n_min`.
3. Write A3 (all scored posts) and A4 (pools: `global`, and one per community).
4. Alias flip. The API's pool cache picks up the new pool at its next leased refresh.

### 5.3 A request to the `bridgeu` feed
1. AppView → `getFeedSkeleton(feed=at://<publisher>/app.bsky.feed.generator/<rkey>)`.
2. Viewer, preferences, flags/arm resolved; exclusions fetched (scope per E3).
3. `run_generate`: `two_tower` (kNN), `bridging_community` (viewer → community via A1, pool from
   cache), `network_likes`; dedup; infill.
4. Hydrate; `run_predict`: `heavy_ranker`, `bridging` (one batched A3 lookup), `perspective`;
   gates (E2); combine; politics multiplier per E7.
5. MMR; cut-offs; skeleton with signed `feedContext`.
6. Snapshot: served order, every generator that returned each served item (exists today), every
   ranker score, full per-generator candidate sets and artifact versions (E4), arm (E5).

### 5.4 Measurement loop
`interactions` (incl. `interactionSeen`) + snapshots → durable export (E6) → DS evaluation:
overlap(relevance, bridging) and source survival from per-item provenance; diverse-approval@k and
cross-community exposure from A1/A3 joined to served items; negative interactions from replies;
engagement from likes/reposts on served items. Results feed the next parameter set.

---

## 6. The version thread

A served post must be traceable to the code and data that put it there:

`api git sha` (on every snapshot, `/health`) + `feed config` (in code at that sha) +
`artifact {name, model_version, run_id}` for A1–A4 (E4) + `arm` (E5) → MLflow run for each
`run_id` → its inputs (graph time, like window, parameters) → raw event partitions.

This mirrors the Eurosky repo's `<corpus-stem>@<sha256[:8]>` feed version string and MySky's
git-sha stamping; neither alone is enough once signals come from another org.

---

## 7. Defaults and failure behaviour

| situation | behaviour | who declares it |
|---|---|---|
| viewer has no community in A1 | `bridging_community` falls back to `bridging_global`; `cross` term = 0 | DS (default in A1 manifest) |
| post absent from A3 or abstained | `bridging` ranker returns the DS-declared default (E1), **not** the slate median | DS |
| A3/A4 older than `max_signal_age` | generator returns empty with reason `stale_signal`; infill covers; metric alerts | MySky (threshold is a parameter) |
| ES alias missing / lookup timeout | degradation event, fail-open (existing framework) | MySky |
| all candidates cut by gates | existing fail-open to pre-cut order | MySky |

---

## 8. Rollout order

1. **Shadow.** Bridging generators run for internal/debug users inside `your-feed` but are only
   recorded (feed_debug / snapshot), not served — overlap and fill-share on real traffic with no
   user-visible change. Requires a small "shadow generators" option on `FeedConfig`.
2. **Internal feed.** `bridgeu` published on the dev account; DS and engineers use it; tune
   parameters from logs.
3. **Public feed.** Published under the BridgEU/Eurosky account (publisher DID may differ from the
   service DID), served by MySky.
4. **Experiments.** Randomized arms within the feed's subscribers (E5, E6), then on Eurosky's
   surfaces.
