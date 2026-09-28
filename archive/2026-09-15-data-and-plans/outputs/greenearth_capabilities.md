# Green Earth API — tested capabilities (with X-API-Key)

**Date:** 2026-09-15 · **Auth:** `X-API-Key` (held by DS). Key is **not** stored
in this repo; `scripts/test_greenearth.py` reads it from `GEA_KEY`.

## Endpoints exercised

| endpoint | method | auth | status | latency | notes |
|---|---|---|---|---|---|
| `/health` | GET | none | 200 | 0.26 s | `{status, git_sha}` |
| `/xrpc/app.bsky.feed.describeFeedGenerator` | GET | none | 200 | 0.20 s | DID + feeds |
| `/skylight/search` | GET | key | 200 | 0.36–0.50 s | `q` (Elasticsearch syntax), `size` ≤ 100 |
| `/candidates/generators` | GET | key | 200 | 0.23 s | 6 generators (below) |
| `/rank/models` | GET | key | 200 | 0.29 s | 5 rankers (below) |
| `/api/feeds` | GET | **session** | 401 | 0.20 s | needs a bearer/session token, **not** the API key |
| `/candidates/generate` | POST | key | 200 | 0.69 s | full `CandidatePost` hydration |
| `/rank/predict` | POST | key | 200 | 0.32–0.51 s | `candidate_score+heavy_ranker`, and `perspective`, both work |
| `/diversify` | POST | key | 200 | 0.39 s | 10 → 10 reordered |

**Generators:** `popularity, random_posts, followed_users, network_likes,
two_tower, two_tower_empty_history`.
**Rankers:** `candidate_score, two_tower, perspective, heavy_ranker,
heavy_ranker_empty_history`.

## Request shapes (from the OpenAPI spec)

- `POST /candidates/generate` — `{generators:[{name,weight}], user_did,
  num_candidates=100, video_only=false, max_age_hours=168, exclude_uris=[],
  hydrate_embeddings=false, infill?}` → `{candidates:[CandidatePost]}`.
- `POST /rank/predict` — `{candidates:[CandidatePost], models:[{name,weight}],
  user_did, politics=1.0}` → `{rankings:[{at_uri, rank, rank_score}]}`.
- `POST /diversify` — `{candidates:[CandidatePost]}` → `{candidates:[...]}`.
- `GET /skylight/search` — `{q, size}` → `{results:[CandidatePost]}`.
- `generators`/`models` are **objects** `{name, weight}`, not strings (passing
  strings → HTTP 422).

## `CandidatePost` — field population by endpoint

| field | `/skylight/search` | `/candidates/generate` |
|---|---|---|
| `at_uri`, `content`, `author_did` | ✓ | ✓ |
| `minilm_l12_embedding` | ✓ | ✓ (with `hydrate_embeddings=true`) |
| `score` | ✓ (ES relevance) | ✓ (generator score) |
| `generator_name` | — | ✓ (`popularity` / `two_tower` / `network_likes` …) |
| `like_count` | **null** | ✓ (e.g. 23 744) |
| `politics_score` ∈ [0,1] | **null** | ✓ (e.g. 0.016, 0.93, 0.405) |
| `contains_images/video`, `image_count`, `video_count` | null | ✓ |

## Data facts that matter downstream

- **`minilm_l12_embedding`** is base64-packed **little-endian float32, 384-d,
  L2-normalized** (‖v‖ = 1.000). Decode: `array('f').frombytes(base64.b64decode(s))`.
  Directly usable for cosine/dot similarity — a ready-made **content embedding**
  for the topic model and the relevance path.
- **`politics_score`** is a per-post partisanship score in **[0, 1]**, spread
  across the range on a sample → a usable **partisanship signal**, but only
  populated by `/candidates/generate` (user-conditioned), **not** by search.
  Methodology/label definition unknown — ask the platform.
- **`like_count`** is a real engagement count, populated by generate only.
- The service is a **working reference implementation** of a multi-source
  retrieve → rank → diversify feed, including a **`two_tower`** generator (the
  §4 CF-upgrade path) and **`perspective`**/`heavy_ranker` rankers.

## What greenearth (keyed) does and does NOT give the DS data layer

**Provides now:** ready-made 384-d normalized content embeddings (per topic via
search, per user via generate), a partisanship `politics_score`, `like_count`,
topic search (Elasticsearch), and a reference generate/rank/diversify pipeline
to benchmark against (incl. two-tower and a Perspective-based ranker).

**Still does NOT provide** (the raw relational data the offline pipeline needs):
- no `(user, post)` **like/repost edges** (CF training data),
- no **follower graph**,
- no **reply/thread** structure,
- no **exposure/impression** logs,
- and `politics_score`/embeddings are **service outputs** (per user-conditioned
  candidate or per topic search), not a bulk raw feature keyed by arbitrary URI.

So the platform data request (`2026-09-15-data-request.md`) stands unchanged for
the raw relational data; greenearth is confirmed useful for the
content-embedding + partisanship + topic-search + reference-pipeline slice.

**Unknowns to confirm with the platform:** rate limits, `politics_score`
definition, corpus freshness/coverage behind Skylight, and whether embeddings/
`politics_score` can be hydrated for an arbitrary list of post URIs (a bulk
hydrate endpoint would be valuable).
