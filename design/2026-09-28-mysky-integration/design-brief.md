# BridgEU × MySky — design brief: what we reuse, what we add, and the interface between teams

**Date:** 2026-09-28 · **From:** BridgEU data science · **For:** MySky (GreenEarth) and Eurosky engineers
**Status:** draft for discussion; decisions marked **[D#]** need sign-off before build.
**Companion docs (same folder):** `implementation-note.md` (how the parts run together),
`wps-revised.md` (scope of work), `api-learnings.md` (evidence for every claim about `./api`).

---

## 1. The ask in one paragraph

BridgEU is a For-You feed that retrieves and ranks for two objectives: **personal relevance**
and **bridging** — posts that earn positive engagement from audiences that normally approve of
different things, where "audience" is defined by like-behaviour and by follow-graph communities
(`concept/design/concept-paper/overview.md`). MySky's API already does everything a production
feed needs *except* the bridging signals: it has the request path, auth, pagination,
exclusions, interaction logging, caching, monitoring, deploy/rollback, and a plugin framework
for candidate generators and rankers. **We propose to ship BridgEU as a feed configuration inside
MySky's API, add two bridging generators and one bridging ranker as plugins, and feed them from
signals computed offline and handed over as versioned artifacts.** No new feed endpoint, no new
serving stack.

---

## 2. What BridgEU needs, in engineering terms

| DS pipeline block | runs | produces | request-path need |
|---|---|---|---|
| O1 Follow graph → communities | batch, refreshed by hand | user → community; community → outgroup(s), banner users, name | viewer's community (1 lookup) |
| O2 Like-graph representation | batch | user and post vectors | — (relevance served by MySky's two-tower) |
| O3 Diverse approval per post | batch, on a cadence (fresh posts) | post → bridging sub-scores (behavioral, network) + approver community shares | batched lookup for ≤ 200 candidates; ordered pools |
| O4 Response quality per post | batch | post → negative-response rate | batched lookup |
| R1 relevance retrieval | request | top-K by user–post affinity | **reuse `two_tower`** |
| R2 bridging retrieval | request (from cache) | top-K by bridging score | **new generator(s)** |
| R3 in-network | request | posts approved by followees | **reuse `network_likes`, `followed_users`** |
| Rank: relevance + bridging + recency − harm | request | ordered slate | reuse `heavy_ranker`; **new `bridging` ranker**; interim harm = `perspective` |
| Diversify | request | final slate | reuse MMR |

The key property: **every corpus-global computation is offline; the request path only looks
things up.** This matches MySky's budgets (4 s per generator, 2.5 s per ranker).

---

## 3. Reuse map for `./api`

| component (`./api/src/app/…`) | verdict | how BridgEU uses it |
|---|---|---|
| `routers/xrpc.py` — `getFeedSkeleton`, `sendInteractions`, cursoring, slate cache, feed snapshots | **reuse as-is** | BridgEU is a `FeedConfig`; the rkey resolves to it |
| `feeds.py` `FeedConfig` registry, debug-feed publishing per git sha | **reuse** | `bridgeu` internal feed first, public later; publisher DID may be a Eurosky/BridgEU account (the router matches on rkey only) |
| `lib/candidates/*` generator ABC + registry + `run_generate` (allocation, parallel, timeout, dedup, infill) | **reuse** | register `bridging_global`, `bridging_community` |
| `two_tower`, `network_likes`, `followed_users`, `popularity` generators | **reuse** | R1 relevance, R3 in-network, infill |
| `lib/candidates/popularity_cache.py` shared pool + leased refresh | **reuse pattern** | bridging pools are viewer-independent (global) or per-community — same cache shape, different key |
| `lib/rankers/*` ranker ABC + `run_predict` | **reuse, extend (E1, E2)** | add `bridging` ranker; see §5.3 for two small framework changes |
| `heavy_ranker`, `perspective` rankers | **reuse** | relevance term; interim stand-in for the harm term |
| `lib/diversify.py` MMR | **reuse, optional extend** | optional community penalty alongside author + content |
| `lib/feed_preferences.py`, `/api/feeds/preferences` | **reuse, extend (E7)** | BridgEU controls; adapter if preferences live outside Firestore |
| `routers/feed_transparency.py` + `FeedSnapshotDocument` | **reuse, extend** | per-item community metadata for the "why this post / which communities" view |
| `lib/pipeline_context.py`, `lib/metrics.py`, `monitoring/` | **reuse** | new generators inherit fill-share, latency, failure metrics and dashboards |
| `interactions` + `interactionSeen` | **reuse, extend (E6)** | exposure log for evaluation and A/B; needs durable export |
| average-user-embedding **artifact → validate → promote → pointer → pin at deploy** (`scripts/promote_average_user_embedding.py`, `lib/average_user_embedding_artifact.py`) | **reuse pattern** | community tables handed from DS to serving |
| PostHog flags (`lib/posthog_client.py`) | **reuse, extend (E5)** | rollout gating now; experiment assignment later |
| `routers/candidates.py`, `routers/rank.py` (HTTP access with API key) | **reuse for offline work** | DS replays candidate/rank stages offline; fallback integration route if **[D2]** goes the other way |

Not reusable for BridgEU without new work: a **global follow graph** (MySky caches only its own
users' outbound follows, ≤ 1 000 each), **reposts** (not visible in ES from `./api`), and
**durable research logs** (snapshots expire after 24 h).

---

## 4. The seam: signals offline, application online

```
   BATCH (DS + Eurosky)                     HANDOFF (versioned)                 SERVING (MySky api)
 ───────────────────────────             ──────────────────────────         ─────────────────────────────
 follow events + snapshot ─► O1 ─► A1 user_community      ─► ES alias ─────► viewer → community
                                └► A2 community_profile   ─► GCS + pointer ─► outgroup, banners, names
 likes (+reposts) window ─► O2/O3 ─► A3 post_bridging     ─► ES alias ─────► bridging ranker lookup
                                  └► A4 bridging_pool     ─► ES / GCS ─────► bridging generators (cached)
 replies window ─────────► O4 ─► A5 post_response_quality ─► ES alias ─────► harm term (later)
```

### 4.1 Artifact contract (applies to A1–A5)

Borrowed from the Eurosky baseline repo's feature contract
(`concept/design/eurosky-repo-learnings/eurosky-repo-learnings.md` §0.3, §4) and MySky's
promotion pattern:

| field | meaning |
|---|---|
| `artifact` | name, e.g. `post_bridging` |
| `model_version` | **meaning** version; bumped whenever the formula changes, never for a re-run |
| `run_id` | UTC timestamp + random suffix; one immutable object per run |
| `keyed_by` | `did` \| `at_uri` \| `community_id` |
| `default` | value served for a missing key — **declared by DS, not imputed by the consumer** |
| `inputs` | graph snapshot time, like window `[t0, t1)`, parameter set (resolution, `n_min`, …) |
| `schema` | JSON schema; validation on promote, as in `average_user_embedding_artifact.py` |

Rules: artifacts are immutable; promotion flips a pointer (GCS `default.json`) or an ES alias;
serving pins the version it loaded and logs it on every snapshot item; rollback is a pointer
flip.

### 4.2 The five artifacts

| id | keyed by | value (sketch) | size | cadence | serving store (proposal) |
|---|---|---|---|---|---|
| A1 `user_community` | did | `{communities:[{id, weight}], primary_id}` | ~10⁶–10⁷ rows | manual (weeks) | ES index behind alias `bridgeu_user_community` |
| A2 `community_profile` | community_id | `{size, outgroups:[{id, score}], banner_dids[], name, name_source}` | 10¹–10³ rows | with A1 | GCS JSON + pointer, loaded in memory |
| A3 `post_bridging` | at_uri | `{n_approvers, community_shares (sparse top-k), z_behavioral, z_network, abstained, window_end}` | ~10⁵–10⁶ rows | parameter `bridging_refresh_minutes` | ES index behind alias `bridgeu_post_bridging` |
| A4 `bridging_pool` | `global` or community_id | ordered `[{at_uri, score}]` of length `pool_size` | small | with A3 | ES or GCS; cached like `popularity_cache` |
| A5 `post_response_quality` | at_uri | `{n_replies, neg_rate}` | as A3 | with A3 | ES alias `bridgeu_post_response_quality` (phase 3) |

Why ES aliases for the large keyed tables: bulk load + atomic alias swap is the cheapest way to
version millions of rows, and the api already has a batched-lookup helper shape
(`fetch_post_embeddings_and_politics_scores`). Why GCS for the small ones: they fit in memory and
the promotion tooling already exists. Alternatives considered are in `implementation-note.md` §4.

Of the four mappings listed in `concept/wps.md` §2, only *user → communities* and
*community → outgroup* are needed on the request path. *Community → ingroup users* is a batch-side
input to O3 and does not need to be served; *community → banner users* and names are needed only by
the transparency/visualization view.

---

## 5. What gets added to `./api`

### 5.1 Generators

| name | input | behaviour | parameters |
|---|---|---|---|
| `bridging_global` | A4 (`global`) | viewer-independent pool, cached with leased refresh; per-request filter by `exclude_uris`, `max_age_hours`, `video_only` | `pool_size`, `refresh_seconds`, `score_field` (`z_behavioral` \| `z_network`), `min_approvers`, `max_per_author` |
| `bridging_community` | A1 + A2 + A4 (per community) | look up viewer's community → pool of posts approved by that community **and** its outgroup(s); falls back to `bridging_global` when the viewer has no community | as above + `min_share_each_side` |

Behavioral and network scores stay **separate generator instances** (e.g.
`bridging_global_network`, `bridging_global_behavioral`) until evidence supports combining
them, following the `two_tower` / `two_tower_empty_history` naming pattern.

### 5.2 Ranker

`bridging` — one batched lookup of A3 for the candidate set; returns a score in declared bounds
`(0, 1)`. The transform from the DS score to [0, 1] (e.g. standard-normal CDF of `z`, or
within-window percentile) is a **DS specification decision**, parameterized as `score_transform`.
Optional personalized term `cross(viewer, post)` from A1 × `community_shares`.

### 5.3 Small framework changes (MySky-owned)

| id | change | reason |
|---|---|---|
| E1 | a ranker may declare `missing_score_policy = "median" \| "default"` with a `default_score` | today a candidate a ranker cannot score receives the **slate median**; for bridging, "no evidence" must map to the null value DS declares, not to the median of neighbours |
| E2 | `FeedConfig.rank_gates`: per-ranker thresholds that demote or drop before combination | the DS ranker has a hard demotion bucket (harm) that a positive-weight average cannot express |
| E3 | `FeedConfig.exclusion_scope = "user" \| "feed"` for seen/discarded posts | today a post seen or discarded in *any* MySky feed is excluded from *every* feed of that user — cross-feed interference for experiments |
| E4 | persist each generator's full returned URI set per request, plus loaded artifact versions | the snapshot already lists, per *served* item, every generator that returned it (pre-dedup); candidates cut before serving survive only in debug records. Overlap(relevance, bridging) needs the full sets; reproducing a slate needs the versions |
| E5 | experiment assignment + arm recorded on snapshot, `feedLoaded`, and `feedContext` | flags today are booleans with no exposure record |
| E6 | durable export of snapshots and interactions (BigQuery or GCS parquet) | snapshots expire after 24 h; research and A/B need weeks |
| E7 | per-feed opt-out of the politics multiplier, or a bridging exemption | the multiplier scales by a politics-topic probability; bridging posts are plausibly political, so a user setting of 0 would remove them by construction |

### 5.4 Feed configuration (illustrative, all values parameters)

```python
"bridgeu": FeedConfig(
    display_name="BridgEU", internal_rkey="be-br", internal_display_name="be BR",
    logged_out="deny",
    gen_request_template=CandidateGenerateRequest.model_construct(
        generators=[GeneratorSpec(name="two_tower", weight=W_REL),
                    GeneratorSpec(name="bridging_community", weight=W_BR),
                    GeneratorSpec(name="network_likes", weight=W_NET)],
        infill="popularity"),
    rank_request_template=RankPredictRequest.model_construct(models=[
        RankModelSpec(name="heavy_ranker", weight=A_REL),
        RankModelSpec(name="bridging", weight=A_BR),
        RankModelSpec(name="perspective", weight=A_QUAL)]),
    exclusion_scope="feed",          # E3
)
```

Generator order matters: dedup keeps the first occurrence, so the order decides which
generator's candidate object (and score) is carried forward when a post is returned by both
pathways. Attribution itself is not lost: the snapshot lists every generator that returned a
served item.

---

## 6. Decisions needed

**[D1] Where do the follow graph and community detection live?** (from `concept/eurosky-engineer.md`)

| option | pros | cons |
|---|---|---|
| A. Eurosky service: Jetstream follow capture + snapshot + rewind + community detection; hands A1/A2 to MySky | European hosting; Eurosky reports an existing similar service; research-grade rewind stays with the team that needs it | cross-org operations; data transfer into MySky's serving stores |
| B. MySky extends ingex (already consumes Jetstream follows) and runs detection | one platform; ingex already parses follow events | MySky has no global graph today; detection is DS research code, awkward to own in a serving org; US-hosted |
| **H (proposed). Split by kind of work** | capture where a production Jetstream consumer already runs; graph materialization, rewind and detection on Eurosky/DS side; only A1/A2 cross the boundary | needs one agreed event-log format |

Proposal: **H**, with capture owned by whichever side already records a complete follow-event
log (verify Eurosky's existing service first; otherwise extend ingex). In all options the
request path touches only A1/A2.

**[D2] At what level do we integrate with MySky?** (a–d in `concept/eurosky-engineer.md`)

Proposal: **inside MySky's API as plugins at both candidate-generation and ranking (a + b + c),
code kept modular so it travels if Eurosky later self-hosts MySky (d).** Considered and set aside
for v1: a Eurosky-hosted feed generator that calls MySky's `/candidates/generate` and
`/rank/predict` over HTTP. It works today with an API key (DS measured 0.7 s and 0.3–0.5 s), but it
rebuilds auth, cursors, exclusions, interaction logging and caches, and it puts a cross-org network
hop on every request. It remains the fallback if contributing code to `./api` is not possible.

**[D3] Is bridging retrieval global, per community, or per user?** (`concept-eval` §3.4)
The framework supports the first two cheaply (cached pools). Proposal: build both generators
and let the experiment decide; per-user bridging retrieval is out of scope for v1.

**[D4] Data residency.** MySky runs in GCP `us-east1`. If BridgEU serves European users,
confirm with the project's data-protection lead whether serving from the US is acceptable or
whether a European deployment of the API is a requirement.

---

## 7. Out of scope for v1

Training a new relevance model (MySky's two-tower and heavy ranker are the relevance terms);
a learned multi-objective ranker; per-user bridging retrieval; a separate BridgEU serving stack;
HaR model training (Perspective is the interim stand-in, which measures the post's own text, not
the responses it will attract — `concept-eval` §3.6).

---

## 8. Questions for MySky engineers

1. `likes` / `replies` index retention; acceptable way to take a rolling window out of ES in bulk
   (export job vs scoped read key) without loading the serving cluster.
2. Are reposts ingested anywhere?
3. Does ingex keep every follow/unfollow event, or only patch cached users?
4. Can BridgEU batch jobs get a write key scoped to `bridgeu_*` indices?
5. Can the inference service host a third model (for a future HaR ranker)?
6. Where does the "Skeets user store" live and what is its read contract?
7. Retention and export options for `interactions` and feed snapshots.
