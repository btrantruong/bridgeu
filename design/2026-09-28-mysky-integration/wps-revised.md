# Scope of work — MySky engineers, BridgEU production feed and experimentation infrastructure

**Date:** 2026-09-28 · **Revises:** `concept/wps.md` · **Status:** draft for DS review, then for MySky/Eurosky
**Owners:** **M** = MySky/GreenEarth engineers (this scope) · **E** = Eurosky engineers · **DS** = BridgEU data science
**Background:** `design-brief.md` (interfaces, decisions D1–D4), `implementation-note.md` (how parts fit).

---

## Goal

1. Bring the BridgEU For-You pipeline into production **as a feed inside MySky's API**, fed by
   community and bridging signals computed offline by DS/Eurosky.
2. Leave behind infrastructure Eurosky can reuse: versioned signal handoff, durable research
   logs, and an experimentation layer for A/B tests on Eurosky.

Principles: *batch fits, serving applies*; *every signal is a versioned artifact with a declared
default*; *every served item is traceable to code, config, artifact versions, and experiment arm*;
*every parameter is configuration, not code*.

---

## What changed from `concept/wps.md`

| original item | revised | why |
|---|---|---|
| 1. Follow graph: snapshot, Jetstream tracker, rewind | kept; rewind is batch-only; tracker must log record keys so unfollows resolve; ownership per D1 | Jetstream delete events carry the record key, not the followed DID; MySky's follow cache today only *invalidates* on unfollow |
| 2. Community mappings "in the serving path" (4 maps + names) | only *user → communities* and *community → outgroups* on the request path; banners/names for the transparency view; *community → ingroup users* stays batch-side | the request path needs 1 viewer lookup; ingroup lists are inputs to batch scoring, and can be millions of rows |
| 3. Bridging candidate generator + "main getFeedSkeleton endpoint" | two generators (global, per-community) + one ranker as MySky plugins; **no new endpoint** — BridgEU is a `FeedConfig` | MySky's endpoint already handles auth, cursors, exclusions, interactions, snapshots, monitoring |
| — | **new:** bridging ranker and two framework changes (default-score policy, gates) | the DS ranker rewards bridging and hard-demotes harm; MySky's combiner imputes medians and has no gates |
| — | **new:** data exports (likes/replies window; served slates and interactions) | batch signals need identity-preserving likes; evaluation needs durable exposure logs (snapshots expire after 24 h) |
| 4. Skeets integration & UI | kept; add a preference-source adapter and extend the existing transparency API with community metadata | the "Skeets user store" is not part of `./api`; transparency already has per-item provenance |
| — | **new:** shadow mode, then experimentation layer (assignment, arm logging, per-feed exclusions) | future A/B tests on Eurosky need assignment and exposure records that do not exist today |

---

## Phases and exit criteria

| phase | contents | exit criterion |
|---|---|---|
| **P0 Agreements** | WP0; decisions D1–D4 signed off | keys issued; questions in `design-brief.md` §8 answered |
| **P1 Data and signals** | WP1, WP2, WP3, WP8 | A1–A4 load end-to-end on stage via alias/pointer flip; exports landing on schedule |
| **P2 Shadow and internal feed** | WP4, WP5, WP6 (shadow, internal) | bridging generators run in shadow for debug users; internal `bridgeu` feed served on stage and prod dev account |
| **P3 Public feed and UI** | WP6 (public), WP7 | public feed published; transparency view shows community metadata |
| **P4 Experimentation** | WP9 | one randomized experiment runs end-to-end with arm-level exposure and outcome tables |

No calendar estimates are given here; they depend on D1/D2 and on answers to the §8 questions.

---

## Work packages

### WP0 — Access and agreements  · M (with DS, E)
- [ ] Scoped credentials: ES write key for `bridgeu_*` indices only; GCS bucket/prefix for BridgEU artifacts; stage access for DS.
- [ ] Dev feed account usage for internal `bridgeu` feed; decision on the public publisher account (BridgEU/Eurosky DID).
- [ ] Data-sharing and data-protection review covering exports of interactions and served slates (D4: US-hosted serving of European users).
- [ ] Answers to `design-brief.md` §8 (retention, reposts, follow-event logging, inference capacity, Skeets store).

### WP1 — Follow graph capture and rewind  · owner per **D1** (M if ingex is extended; E if Eurosky's service is used)
- [ ] Append-only follow-event log from Jetstream: `{time_us, did, op ∈ {create, delete}, rkey, subject_did (create only)}`.
- [ ] Resolve deletes to edges via an `rkey → subject` map seeded from the snapshot and maintained from creates. **The snapshot must carry follow record keys** (e.g. from `com.atproto.repo.listRecords`); if the existing S3 snapshot lacks them, record the size of the unresolved-unfollow gap.
- [ ] Point-in-time reconstruction `graph(t) = snapshot(t0) ⊕ events(t0, t]` as a batch job (research use; no live query API).
- [ ] Monitoring: consumer lag, cursor gaps/reconnects, events/day.
- **Acceptance:** for sampled DIDs, `graph(t)` matches a fresh crawl at `t` within tolerance `graph_rewind_tolerance`; lag below `follow_log_max_lag`.
- **If Eurosky's existing service already produces this log:** WP1 for M reduces to reviewing the format.

### WP2 — Community signals in the serving path  · M (DS produces A1/A2)
- [ ] Loader for **A1 `user_community`**: bulk-load into a new ES index, validate, flip alias `bridgeu_user_community`; rollback = flip back.
- [ ] Loader for **A2 `community_profile`** (outgroups, banner DIDs, names): GCS artifact + `default.json` pointer, validated with JSON schema and pinned per deployment — same pattern as `promote_average_user_embedding.py`.
- [ ] Lookup helpers: `viewer_communities(did)`, `outgroups(community_id)`, `profile(community_id)`; return the manifest's declared default on a miss; request-scoped caching.
- [ ] Log A1/A2 `{model_version, run_id}` on every snapshot that used them.
- **Acceptance:** alias/pointer flip changes served lookups without a code deploy (A1) or with a pinned deploy (A2); p95 lookup latency below `community_lookup_p95_ms`.
- **Not in scope for M:** detection, outgroup definition, banner selection, name generation (DS; names get human review before they are shown).

### WP3 — Inputs for bridging signals  · M (E runs the batch job, DS owns the estimator)
- [ ] Scheduled export of the like window from ES: `{liker_did, subject_uri, created_at, like rkey}` plus tombstones, partitioned by hour, to object storage readable by E/DS.
- [ ] Same for `replies` (with parent URI) for response quality (phase 3).
- [ ] Reposts: confirm whether ingex can add them; if not, E captures them from Jetstream.
- [ ] ES index template + alias `bridgeu_post_bridging` (A3) and pool storage (A4) that E's job writes with the WP0 key.
- [ ] Freshness monitor: age of the live A3/A4 version; alert above `max_signal_age`.
- **Acceptance:** export completeness (row counts vs ES) within `export_completeness_tolerance`; A3 refresh visible in serving within one pool-cache cycle.

### WP4 — Bridging retrieval  · M
- [ ] `bridging_global`: viewer-independent pool from A4, cached in Firestore with a leased background refresh (copy `popularity_cache.py`); per-request filtering by `exclude_uris`, `max_age_hours`, `video_only`; per-author cap.
- [ ] `bridging_community`: viewer → community (WP2) → per-community pool; fallback to global when the viewer has no community.
- [ ] One registered instance per score kind (`…_behavioral`, `…_network`) so the two pathways are measured separately.
- [ ] Parameters (not constants): `pool_size`, `refresh_seconds`, `min_approvers`, `min_share_each_side`, `max_per_author`, `score_field`.
- [ ] Unit tests in the repo's style; existing metrics (`fill_share`, latency, failures) must appear per generator.
- **Acceptance:** p95 generator latency below `bridging_generator_p95_ms` (well inside the 4 s timeout); fill share reported per community.

### WP5 — Ranking  · M (DS specifies the transform and defaults)
- [ ] `bridging` ranker: one batched A3 lookup per slate; declared bounds (0, 1); `score_transform` parameter; optional `cross(viewer, post)` term from A1 × approver community shares.
- [ ] **E1** missing-score policy per ranker (`median` today; `default` with a declared value).
- [ ] **E2** `rank_gates` in `FeedConfig`: per-ranker threshold → demote or drop, applied before combination.
- [ ] **E7** per-feed control of the politics multiplier (off, or bridging exempt).
- [ ] Optional `recency` ranker (bounded decay, parameterized half-life).
- **Acceptance:** changing any weight, gate or transform is a config change; `feed_debug` shows per-ranker normalized scores and gate actions for each item.

### WP6 — BridgEU feed configuration, shadow mode, publishing  · M
- [ ] **Shadow generators** option on `FeedConfig`: listed generators run and are recorded in `feed_debug`/snapshot but do not enter the slate. Enable for debug users on `your-feed`.
- [ ] **E3** `exclusion_scope = "user" | "feed"` for seen/discarded posts; `bridgeu` uses `"feed"`.
- [ ] **E4** persist each generator's full returned URI set per request and the loaded artifact versions. Today the snapshot lists, for each *served* item, every generator that returned it; the recorder already holds the full per-generator outputs on every request but persists them only for debug users.
- [ ] `bridgeu` `FeedConfig` (generators, rankers, gates, cut-offs, controls) — internal first, public in P3.
- [ ] Publish the public feed record under the agreed publisher DID pointing at MySky's service DID.
- **Acceptance:** from shadow logs DS can compute overlap(relevance, bridging) per request; internal feed passes the existing load test at the target QPS `bridgeu_target_qps`.

### WP7 — Client integration and transparency UI  · M (with E for the Skeets side)
- [ ] Preference-source adapter: `getFeedSkeleton` reads BridgEU controls from the Skeets/Eurosky user store (contract to be agreed) or from MySky Firestore; fail-soft to declared defaults.
- [ ] Port MySky feed controls that apply to BridgEU (freshness, source weights, purpose) — list agreed with DS.
- [ ] Extend `/api/feeds/{request_id}` item metadata with: approver community shares, the viewer's community, ingroup/outgroup names, banner users (from A2/A3).
- [ ] Data endpoint for the graph visualization (communities of a post's engagers and their relation); rendering owned by the client team.
- **Acceptance:** for any served bridging post the transparency view shows which communities approved it, with names and banner accounts.

### WP8 — Durable research logging  · M
- [ ] **E6** export feed snapshots (served order, per-item provenance, scores, artifact versions, arm) and `interactions` (incl. `interactionSeen`) to BigQuery or partitioned parquet, retained for `research_log_retention_days`.
- [ ] Documented schemas; load-test traffic excluded; pseudonymization policy agreed in WP0.
- [ ] **E5 (part)** record the served arm / flag values on snapshot, `feedLoaded` and `feedContext`.
- **Acceptance:** DS can join served items → exposures (`interactionSeen`) → outcomes (like/repost/reply) for any day in retention.

### WP9 — Experimentation layer (A/B on Eurosky)  · M builds, E adopts, DS designs experiments
- [ ] Assignment: deterministic `hash(experiment_id, salt, did) → arm`, or PostHog multivariate flags with explicit exposure capture (the current batch flag API emits no exposure events). Choice recorded with reasons.
- [ ] Arm → `FeedConfig` patch, composed in the same place user preferences are applied today (`_configured_generation`); arm included in the preference fingerprint and slate-cache key so arms never share cached slates.
- [ ] Holdouts, ramp percentages, kill switch (config, no deploy); guardrail metrics: latency, empty-slate rate, degradation events, per-arm fill share.
- [ ] Interference controls: per-feed exclusions (E3); report users active in several feeds.
- [ ] Analysis-ready tables from WP8: exposure, outcome, and guardrail metrics per arm per day.
- [ ] Packaged as a module with no MySky-specific dependencies beyond the config/snapshot interfaces, so Eurosky can run it if it self-hosts the API (D2 option d).
- **Acceptance:** an A/A test on the internal feed shows no arm difference in guardrails beyond the pre-registered tolerance; arm assignment is reproducible from `(experiment_id, did)`.

---

## Responsibilities outside this scope (dependencies for M)

| item | owner | needed by |
|---|---|---|
| D1–D4 sign-off | DS + E + M leads | P0 |
| Community detection, outgroups, banners, names → A1/A2 | DS (E schedules) | WP2 |
| Diverse-approval estimator (null model, sample-splitting, shrinkage) → A3/A4 | DS (E schedules on Dagster) | WP4, WP5 |
| Score transform, defaults, gate thresholds, weights | DS | WP5, WP6 |
| Evaluation of shadow/internal logs; experiment designs and pre-registration | DS | P2–P4 |
| Skeets user-store contract; client rendering of the visualization | E | WP7 |
| HaR model (replacing `perspective` as the harm term) | DS; hosting on inference service to be agreed | after P3 |

---

## Parameters referenced above (values set in P0/P1, not in code)

`graph_rewind_tolerance`, `follow_log_max_lag`, `community_lookup_p95_ms`,
`export_completeness_tolerance`, `max_signal_age`, `bridging_refresh_minutes`, `pool_size`,
`refresh_seconds`, `min_approvers`, `min_share_each_side`, `max_per_author`, `score_field`,
`score_transform`, `bridging_generator_p95_ms`, `bridgeu_target_qps`,
`research_log_retention_days`, generator weights, ranker weights, gate thresholds.
