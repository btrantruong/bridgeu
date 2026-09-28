# `./api` (GreenEarth / MySky API) — learnings for the next session

**Date:** 2026-09-28
**Scope:** read-only study of `./api` at commit `868e9a6` (`main`, PR #505), plus the public
DeepWiki pages for `greenearth-social/api` (indexed 2026-03-10, commit `67a47e`) and
`greenearth-social/ingex`. **Code wins where the wiki disagrees** — the wiki is six months older
than the checkout and describes generators (`post_similarity`) that no longer exist.
**Purpose:** agent-facing reference. Read this instead of re-reading `./api`.
**Provenance tags:** `[code]` verified in `./api`; `[wiki]` DeepWiki only; `[inferred]` deduced
from comments in `./api` about sibling repos that are not in this checkout.

---

## 0. One-paragraph summary

`./api` is the **serving layer** of the MySky feed family (brand account `mysky.social`,
built by GreenEarth). It is a FastAPI service on Cloud Run (GCP project `greenearth-471522`,
`us-east1`, stage + prod) that implements the AT Protocol feed-generator endpoints and an
internal, API-key-protected recommendation API. Each feed is a **declarative `FeedConfig`**: a
weighted mix of named **candidate generators**, an optional weighted mix of named **rankers**,
MMR diversification, and slate cut-offs. Generators and rankers are plugins behind two narrow
abstract classes and a name registry. The service holds **no training code and no batch
jobs**: content, likes and embeddings come from Elasticsearch filled by the `ingex` repo; ML
predictions come from an HTTP inference service in the `engagement-prediction` repo; user state
lives in Firestore; analytics and feature flags in PostHog. It is **stateless per request apart
from Firestore caches**, and every stage is instrumented, time-boxed, and fail-open.

---

## 1. The system around `./api`

```
          Bluesky network
   ┌───────────┴─────────────┐
 Jetstream (likes, follows)   Graze megastream (posts + precomputed MiniLM embeddings)
   │                          │
   ▼                          ▼
 ingex  (Go, GKE/Cloud Run)  ───────────────►  Elasticsearch 9.x on GKE (private VPC)
   │  also: Perspective scoring at ingest,      indices: posts, posts_recent,
   │  topic_scores, quality corpus promotion,   posts_recent_quality, likes, replies
   │  follow deltas → Firestore                 (+ tombstones, hashtags, inferences [wiki])
   ▼                                                   │ readonly API key
 Firestore  ◄──────────────────────────────────►  api (FastAPI, Cloud Run)  ◄── Bluesky AppView
   users, interactions, seen/discarded,               │    getFeedSkeleton / sendInteractions
   feed_snapshots, caches                             │
                                                      ├──► inference service (engagement-prediction)
 frontend (Firebase; owns Firestore rules/indexes)    │      /models/user-tower/predict
   Settings UI → /api/feeds/preferences               │      /models/ranker/predict, /ready
                                                      ├──► Perspective API (fallback scoring)
                                                      └──► PostHog (events + feature flags)
```

| repo / service | role | evidence |
|---|---|---|
| `api` | serving: feed generator + internal recommendation API | `[code]` |
| `ingex` | Go ingest: Graze megastream posts (with embeddings) + Jetstream likes → ES; tombstone pattern for deletes; ES ~30-day retention, prod 2 master + 4 data nodes | `[wiki]` |
| `ingex` (cont.) | scores posts with Perspective at ingest (9 000 RPM share); promotes `like_count ≥ 20` posts into `posts_recent_quality` (ingex#442); consumes Jetstream **follow** events to patch `followed_users_cache` | `[inferred]` from `README.md`, `lib/elasticsearch.py`, `lib/followed_users_cache.py` |
| `engagement-prediction` | trains the two-tower model and the heavy ranker; hosts the inference service; model buckets `greenearth-471522-engagement-prediction-model-{stage,prod}` | `[inferred]` from `lib/inference.py`, `README.md` |
| `frontend` | Firebase app; owns Firestore rules, indexes, TTL policies and the emulator | `[code]` README |
| `internal-tools/devenv` | publishes dev feeds | `[code]` README |

---

## 2. Endpoints

| endpoint | auth | purpose |
|---|---|---|
| `GET /xrpc/app.bsky.feed.getFeedSkeleton` | AT Protocol JWT (from AppView) | the feed; ordered `at://` URIs + cursor + signed `feedContext` per item |
| `GET /xrpc/app.bsky.feed.describeFeedGenerator`, `/.well-known/did.json` | none | feed-generator identity |
| `POST /xrpc/app.bsky.feed.sendInteractions` | none; trust = HMAC-signed `feedContext` | receives `interactionSeen/Like/Repost/Reply/Quote/Share`, `clickthrough*`, `requestMore/Less` |
| `POST /candidates/generate`, `GET /candidates/generators` | `X-API-Key` | run the generator stage stand-alone |
| `POST /rank/predict`, `GET /rank/models` | `X-API-Key` | run the ranker stage stand-alone |
| `POST /diversify` | `X-API-Key` | MMR on a candidate list |
| `GET /skylight/search`, `/skylight/similar` | `X-API-Key` | ES full-text / kNN search |
| `POST /embeddings/user` | `X-API-Key` | two-tower user embedding for one DID (offline use) |
| `GET/PATCH /api/feeds/preferences`, `GET /api/feeds[/{request_id}]`, `POST /api/feeds/{feed}/preview` | Firebase custom token | feed transparency ("why this post"), per-feed user controls, previews |
| `/r/{slug}`, `/admin/redirects` | none / admin key | UTM redirects |
| `GET /health` | none | `{status, git_sha}` |

API keys: `gea_<8-char id><48-char secret>`, SHA-256-hashed in Firestore `api_keys`, monthly
call counters, `admin` flag. The DS already holds one (see
`concept/archive/2026-09-15-data-and-plans/outputs/greenearth_capabilities.md`).

---

## 3. The request path of `getFeedSkeleton`, end to end `[code]`

`routers/xrpc.py` (2 449 lines) — the single most important file.

1. **Resolve feed.** `rkey` = last segment of the `feed` AT-URI; matched against `FEEDS` by
   canonical name or `internal_rkey`. The publisher DID is ignored — **a feed record owned by any
   account can point at this service**.
2. **Identify viewer.** JWT verified (`lib/atproto_auth.py`); logged-out behaviour per feed:
   `explain` (serve one UX post), `serve` (anonymous pipeline), `deny` (401).
3. **Flags.** PostHog `get_all_flags(did, keys)` for `fail-fast-feed`,
   `expanded-candidate-batch`, `network-likes-in-your-feed`. Batch API → **no
   `$feature_flag_called` exposure events are emitted**, and flag values are **not** written to
   `feedLoaded` or the feed snapshot.
4. **Preferences.** Firestore `users/{did}` → `resolve_feed_preferences()` → request-local
   `FeedConfig` via `model_copy`:
   - `source_weights` {following, network_likes, authors_topics, popular} → generator weights;
   - `freshness` 0–5 → `max_age_hours` ∈ {6, 12, 24, 48, 72, 168};
   - `purpose` 0.2–0.8 → weight of `perspective` = purpose, of `heavy_ranker` = 1 − purpose;
   - `politics` 0–2 → multiplier on the final score, scaled by the post's
     `topic_scores["News & Social Concern"]`.
   A SHA-256 **preference fingerprint** of the realized config is kept with the slate.
5. **Exclusions.** Recently **seen** posts (from `interactionSeen`) + recently **discarded**
   posts (cut by `min_rank_score`). Both are stored **per user, not per feed**
   (`record_seen_posts(db, user_did, …)`, `get_recent_discarded_uris(db, user_did)`).
6. **Batch size.** A new cursor session generates `min(limit × 5, 100)` candidates (200 with the
   expanded flag); the template's `num_candidates=30` is overridden. The slate is cached in
   Firestore (`feed_cache`, 10-min TTL) and paged by an opaque cursor `{id, offset, v}`.
7. **Generate** — `lib/candidates/generate.py::run_generate`:
   - largest-remainder allocation of `num_candidates` across generators by weight;
   - all generators in parallel, each under `GE_CANDIDATE_GENERATOR_TIMEOUT_SEC` (4 s);
   - failures/timeouts → empty result + `DegradationEvent` (hard-fail only under the
     `fail-fast-feed` flag);
   - dedup by `at_uri`, **first occurrence wins** (generator order = config order);
   - optional `infill` generator tops up a shortfall under the same freshness window.
8. **Hydrate** MiniLM embeddings + politics scores in one batched ES call (1.5 s budget).
9. **Rank** (if the feed has `rank_request_template`) — `lib/rankers/predict.py::run_predict`:
   - candidates without a MiniLM embedding are dropped first;
   - rankers in parallel, 2.5 s each (`GE_RANK_MODEL_TIMEOUT_SEC`); a timed-out ranker contributes
     nothing, any other error propagates;
   - raw scores **linearly normalized to [0, 1] by each ranker's declared theoretical
     `score_bounds`**, clamped;
   - a candidate missing one ranker's score gets **that ranker's slate median**; missing from all
     rankers → dropped;
   - combined = weighted average (weights renormalized to sum 1; weights must be `> 0`);
   - × politics multiplier; sort desc, ties by input order.
   - Rank failure → fail-open to generator-score order.
10. **Cut-offs** (`FeedConfig`): `min_rank_score` floor on combined score (cut posts recorded as
    *discarded*) → MMR (`lib/diversify.py`: `BETA=0.7` diversity, author vs content penalty 0.5/0.5,
    position decay τ=15, cosine on MiniLM) → `min_mmr_score` → `max_render_share` cap. If cut-offs
    empty the slate, **fail open** to the pre-cut order.
11. **Respond** with skeleton items, each carrying a signed `feedContext`
    `{did, feed, rid, iat, lt}`; pinned/survey UX posts inserted. Background tasks write the
    **feed snapshot** (`users/{did}/feed_snapshots/{rid}`: served order + per-item generator,
    model scores, politics adjustment, MMR data; **24 h TTL**), the PostHog `feedLoaded` event,
    feed activity, and — for debug-flagged users — a full `feed_debug` record.
12. **Interactions** arrive later on `sendInteractions`; the token is verified, one Firestore
    `interactions` doc per event is written, `interactionSeen` feeds the seen-posts buckets, and
    events go to PostHog (load-test traffic excluded).

Whole-request budget `GE_FEED_REQUEST_TIMEOUT_SEC` = 9 s.

---

## 4. The two plugin seams `[code]`

### 4.1 Candidate generator (`lib/candidates/base.py`)

```python
class CandidateGenerator(ABC):
    name: str                                    # registry key, referenced by FeedConfig
    async def generate(self, es, user_did, num_candidates=100, video_only=False,
                       exclude_uris=None, max_age_hours=168) -> CandidateResult
# CandidateResult{generator_name, candidates: list[CandidatePost], status, reason, mode}
register_generator(instance)                     # at import, in candidates/__init__.py
```

Parameterized variants are separate instances under different names
(`TwoTowerCandidateGenerator(name="two_tower_empty_history", history_mode="empty")`).

| generator | what it returns | score it sets | store |
|---|---|---|---|
| `followed_users` | newest posts by accounts the viewer follows (≤ 1 000 follows) | ES | `posts_recent` |
| `network_likes` | posts liked recently by accounts the viewer follows; one overfetched likes scan + one hydrate | # of followed accounts that liked it | `likes` → `posts_recent` |
| `popularity` | recency-Gaussian × log1p(like_count) `function_score`; **one shared pool per (freshness, video_only)** cached in Firestore with a lease-guarded background refresh | ES function score | `posts_recent` + `popularity_cache` |
| `random_posts` | random recent posts | random | `posts` |
| `two_tower` / `two_tower_empty_history` | user tower (inference service) on latest 64 likes → kNN on `embeddings.ge_post_embedding` filtered by post-tower model UUID | kNN similarity | `posts_recent_quality` (2 wk, like_count ≥ 20) |
| `llm_query_vector` | kNN on a Firestore-stored query vector | kNN | **not registered** (TODO in file) |

### 4.2 Ranker (`lib/rankers/base.py`)

```python
class Ranker(ABC):
    name: str
    score_bounds: tuple[float, float]            # theoretical (min, max) of raw scores
    async def predict(self, es, user_did, candidates) -> RankerResult
# RankerResult{model, result: RankPredictResult{rankings:[{at_uri, rank, rank_score}]}}
```

| ranker | score | bounds |
|---|---|---|
| `heavy_ranker` / `_empty_history` | engagement model via `/models/ranker/predict`, user history features from `user_history_cache` | (0, 1) |
| `perspective` | Perspective "PRC" conversational-quality score, **precomputed at ingest** (`combined_perspective_score`), live API fallback | `PERSPECTIVE_SCORE_BOUNDS` |
| `candidate_score` | the generator's own score | (0, 1) |

### 4.3 Feed registry (`src/app/feeds.py`)

`FEEDS: dict[str, FeedConfig]`. Relevant `FeedConfig` fields: `gen_request_template`
(generators+weights, infill), `rank_request_template` (rankers+weights), `diversify`,
`exclude_seen_posts`, `min_rank_score`, `min_mmr_score`, `max_render_share`, `controls`,
`preference_source`, `logged_out`, `public`, `internal_rkey`. Public feeds today: `your-feed`
("MySky"), `best-of-friends`, `random`. Everything else is a private debug feed published as
`GE <internal_display_name> <sha>` on the dev account. **Adding a feed is a config entry; adding
a signal is one generator or ranker class.**

---

## 5. Data available inside the platform

### 5.1 Elasticsearch `[code]` + `[wiki]`

| index | content | notes for BridgEU |
|---|---|---|
| `posts` | all retained top-level posts | ~30-day retention `[wiki]` |
| `posts_recent` | ~1 week alias, top-level posts only | default candidate corpus |
| `posts_recent_quality` | 2 weeks, `like_count ≥ 20` only | two-tower kNN corpus |
| `likes` | one doc per like: `author_did` (= **liker**), `subject_uri`, `created_at` | **identity-preserving (who, what) edges** — enough for CF and diverse-approval sets |
| `replies` | replies with content/embeddings | usable for response-quality (O4); parent-link field not verified |

Post fields used by serving: `at_uri, author_did, content, created_at, like_count,
contains_{images,video}, image_count, video_count, external_embed,
embeddings.all_MiniLM_L12_v2` (384-d, L2-normalized), `embeddings.ge_post_embedding` +
`ge_post_embedding_model_uuid` (two-tower post tower), `topic_scores` (incl.
`"News & Social Concern"`), `combined_perspective_score`, `perspective_scored_at`.

**Not in ES (verified by absence in `./api`):** reposts, quote targets, a follow graph,
follower (inbound) lists.

### 5.2 Firestore collections `[code]`

| collection | key | holds | retention |
|---|---|---|---|
| `users` | DID | handle, flat legacy prefs, `feed_preferences[feed]`, `debug_feeds` | — |
| `users/{did}/feed_snapshots` | request id | served order + per-item provenance and scores | **24 h** |
| `users/{did}/seen_posts` | UTC day | URIs reported via `interactionSeen` | 14 d |
| `users/{did}/discarded_posts` | UTC day | URIs cut by `min_rank_score` | TTL |
| `interactions` | auto id | one doc per `sendInteractions` event: did, item, event, feed, request id, served time | no TTL found |
| `followed_users_cache` | DID | follow list (≤ 1 000) + `pending_adds` from Jetstream + `invalidated_at` on unfollow | 30 d |
| `user_history_cache` | DID | latest 64 likes + embeddings | fresh 10 min, hard 30 min |
| `popularity_cache` | (window, video) | shared candidate pool | 5 min TTL |
| `feed_cache` | request id | a cursor session's slate | 10 min |
| `feed_debug`, `feed_activity`, `api_keys`, `llm_query_vector`, `redirects` | | | |

### 5.3 Signals worth reusing

- **Exposure:** `interactionSeen` via `sendInteractions` is a real per-user, per-post
  impression log for MySky-served feeds — the one data type the DS data request
  (`2026-09-15-data-request.md` §B.5) rated highest-value and platform-only.
- **Served slates with provenance:** feed snapshots record which generator produced each item and
  every ranker's normalized score — but only for 24 h.
- **Content embeddings:** MiniLM-L12 384-d on every post; two-tower post embeddings on the quality
  corpus.
- **Partisanship proxy:** `topic_scores["News & Social Concern"]` ∈ [0, 1] (a topic
  probability, not a stance score).
- **Conversational quality:** Perspective PRC, precomputed at ingest.

---

## 6. Operational patterns worth copying

| pattern | where | why it matters for BridgEU |
|---|---|---|
| **Shared viewer-independent pool with leased background refresh** | `lib/candidates/popularity_cache.py` | exactly the shape of a global bridging retriever (§concept-eval 3.4) |
| **Immutable artifact → validated → promoted → `default.json` pointer → pinned at deploy** | `scripts/average_user_embedding.py`, `promote_average_user_embedding.py`, `lib/average_user_embedding_artifact.py` (JSON-schema) | exactly the shape of a hand-refreshed community table handed across orgs |
| Stale-while-refresh caches with Firestore leases, fail-open reads | `user_history_cache.py`, `followed_users_cache.py` | per-user/per-community lookups on the request path |
| Per-request degradation ledger + fail-fast flag | `lib/pipeline_context.py` | a new generator cannot break the feed |
| Per-generator metrics (`fill_share`, `success/failure_count`, durations) to Cloud Monitoring; slow-ES-query logging; dashboards in `monitoring/` | `lib/metrics.py`, `lib/telemetry.py` | SLOs for bridging generators come for free |
| Debug feeds per git sha, `feed_debug.py` CLI, transparency API | `feeds.py`, `scripts/feed_debug.py`, `routers/feed_transparency.py` | inspect "why this bridging post" per user |
| Clean-tree deploys stamped with git sha, one-command rollback | `scripts/deploy.sh`, `rollback.sh` | reproducibility of what was served |
| Load-test identities excluded from analytics | `lt` claim, `scripts/load_test/` | experiment data hygiene |

CI: GitHub Actions, `pyright` + `pytest` on push/PR to `main`/`develop`. Tests sit next to
modules (`*_test.py`); `xrpc_test.py` alone is 6 192 lines.

---

## 7. Constraints that shape a BridgEU integration

1. **Small slates.** ≤ 100 (≤ 200 flagged) candidates per cursor session, split by weight. A
   bridging generator at weight 0.2 contributes ~20. The DS design assumed |S| ≈ 500–1 000.
2. **Latency.** 4 s per generator, 2.5 s per ranker, 9 s per request — anything corpus-global must
   be precomputed; the request path may only look it up.
3. **Rank combination is fixed-form.** Bounds-normalized weighted average, positive weights only,
   median imputation for missing scores, no hard gates except a floor on the *combined* score,
   then a user-controlled politics multiplier.
4. **Dedup precedence = config order.** A post returned by two generators keeps the first
   generator's candidate object. Attribution survives: the snapshot's per-item `generators` list
   holds every generator that returned a *served* item, with a within-generator min–max score
   (`FeedDebugRecorder.build_pipeline_metadata` in `lib/feed_debug.py`). The recorder is
   installed on **every** request (`routers/xrpc.py` `_run_pipeline_capturing`), so full
   per-generator outputs exist in memory each time, but candidates cut before serving are
   persisted only in `feed_debug` records (debug users). Candidate-level
   overlap(relevance, bridging) therefore needs a small persistence change, not new capture.
5. **No global follow graph.** Only outbound lists of MySky users, capped at 1 000. Unfollows only
   *invalidate* a cached list (consistent with Jetstream delete events carrying the record key,
   not the followed DID).
6. **Exclusions are per user across feeds.** A post seen or discarded in one MySky feed is
   excluded from every other feed of that user.
7. **Experiment support is flag-level only.** Boolean PostHog flags; no assignment logging on the
   served slate; snapshots expire after 24 h.
8. **Region.** Everything runs in GCP `us-east1`.

---

## 8. Open questions (for GreenEarth / MySky engineers)

1. `likes` index retention and whether a bulk, time-ranged export (not request-path queries) is
   acceptable load; same for `replies` (and its parent-URI field).
2. Are reposts ingested anywhere? (Not visible from `./api`.)
3. Does ingex already log every Jetstream follow/unfollow event, or only patch cached users?
4. `topic_scores` model and label set; `politics_score` definition.
5. Can the `interactions` collection and feed snapshots be exported durably for research?
6. Who owns the inference service contract (`/models/*`) and can it host a third model?
7. "Skeets user store" (named in `concept/wps.md`) does not appear anywhere in `./api`;
   preferences come from Firestore via `/api/feeds/preferences`. What and where is it?
