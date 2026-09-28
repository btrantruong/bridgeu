# Plan — a bridging ranker that rewards civil, cross-community approval

**Date:** 2026-09-02
**Status:** plan only; nothing implemented. Variant A awaiting approval, Variant B added at the user's request as an alternative operationalization of "diverse approval".
**Goal (as stated):** rank up posts that attract a diverse audience and elicit positive responses; rank down posts that elicit negative responses.
**Context:** repo architecture, feature roster, and ranker seams are described in [../2026-09-02-repo-learnings/2026-09-02-repo-learnings.md](../2026-09-02-repo-learnings/2026-09-02-repo-learnings.md).

---

## 0. Measured constraints

Profiled against the slice already on disk, `data/raw/events/probe.jsonl` (10-minute window, 184,992 events). These numbers set the design; they are not results about feed quality.

| quantity | value | consequence for the design |
|---|---|---|
| likes / reposts / posts | 135,855 / 20,918 / 28,219 | — |
| replies (posts carrying a parent uri) | 12,011, **with text** | reply edges *and* reply text are available in the raw zone |
| quote posts | **not captured** | `parse_event` reads `record["reply"]["parent"]["uri"]` only; an embed record's uri is dropped |
| replies targeting a top-200 corpus post | 275 over 123 posts; **≥3 replies: 26 posts** | reply-derived tone is underpowered on a 10-minute slice |
| corpus like-graph | 2,560 likers × 200 posts; **80.0% of likers cast exactly one corpus like** (78.9% within the largest component) | a per-user viewpoint is not identified from corpus edges alone |
| unrestricted like-graph | 35,524 likers × 73,513 posts; median 2 likes/user; 39% have ≥3 | usable as a fit set |
| corpus likers with ≥1 like *outside* the corpus | 1,839 (71.8%); ≥3 likes: 1,125 (43.9%) | sample-splitting is feasible without losing most of the audience |
| like-graph SVD, dim 16, full slice | 24,532 users × 51,998 posts in **1.6 s** | fitting on the whole slice is cheap; cost is not the constraint |
| largest-component restriction, corpus graph | 2,042 users × 137 posts | ~68% post coverage; the rest take declared defaults |

Two of these drive everything below: **the audience-diversity signal must not be measured on the same edges the viewpoint model was fitted on**, and **reply coverage at 10 minutes is too thin for tone to carry the ranker**.

---

## Variant A — continuous viewpoint axis from the like graph

### A.1 The scoring model

For post *p* with engager set *E(p)*, each engager *u* carrying a viewpoint vector *f_u*:

```
score(p) = w_d · diversity(p) + w_e · log1p(positive_engagement(p))
         + w_t · tone(p) − w_n · negative_rate(p)
```

- **diversity(p)** — dispersion of *{f_u : u ∈ E(p)}*: sign-balance on the principal axis (`1 − |mean sign|`), shrunk toward the corpus mean to control small-audience inflation.
- **positive_engagement(p)** — likes + reposts, reusing the existing `popularity` weights.
- **tone(p)** — valence of the *actual replies* to *p*.
- **negative_rate(p)** — share of replies scored negative, plus reply-to-like ratio as a controversy proxy.

Every `w_*`, threshold and axis choice is a CLI parameter. No baked constants.

### A.2 Steps

**S1 — Longer slice.** `jetstream-slice --minutes 180 --out raw/events/probe-3h`, then `corpus-from-slice`. Reply coverage scales with window length; 26 posts with ≥3 replies is not a base for a tone feature. Network step, run once.

**S2 — `viewpoint` feature (user-keyed).** New owner module `features/viewpoint.py`, reusing `likes_spectral.compute_embeddings` as library code but changing *what it is fitted on*: a parameterized **fit set** of like edges that excludes the corpus posts (default) or uses an earlier disjoint window. Emits `{vector, axis, n_likes_fit}` per did, `axis` being the coordinate on the principal direction of the user cloud, sign-normalized deterministically. Parameters: `--dim`, `--min-likes`, `--fit-exclude corpus|window`, axis definition. `DEFAULT` = zero vector / `axis: 0.0` / `n_likes_fit: 0`, so an unmeasurable user contributes nothing rather than a fabricated centrist.

**S3 — `audience_diversity_spectral` feature (post-keyed).** Reads raw events plus the `viewpoint` artifact **by ref**. Per corpus post: engagers from likes ∪ reposts (∪ repliers, parameterized), keep those carrying a viewpoint, emit `{n_engagers, n_with_viewpoint, balance, dispersion, shrunk_balance}`. Shrinkage toward the corpus mean with parameterized strength; `n_with_viewpoint` travels with the value so consumers can gate on coverage. `DEFAULT` = corpus-mean balance — the "unknown" analogue of `popularity`'s median percentile, never the maximum.

**S4 — `response_tone` feature (post-keyed).** Reply edges into corpus posts, scored on reply text. v1 is a deterministic lexicon heuristic in the same explicit stand-in role `baseline/scorers.py` plays for Dante's transformers: same output range, swappable for a fine-tuned classifier without touching consumers. Emits `{n_replies, positive_rate, negative_rate, mean_valence, reply_like_ratio}`. `--min-replies` decides where the feature abstains rather than reporting noise as tone.

**S5 — The ranker.** New workspace package `packages/civic/` (deps `algo-interface`, `data-engineering`, one CLI). Reads the corpus and the three feature artifacts by ref, checks each `meta.model_version`, looks up with declared defaults, applies the score with weights as flags, emits the feed doc via `feed_doc`/`emit`. `publish-serving --feed` then `bridgeu-serve` serves it under rkey `civic` — no serving code in the package.

**S6 — Eval metrics.** Add to `evals.metrics`: `audience_balance@k`, `cross_side_exposure@k`, `negative_response@k`, `viewpoint_dispersion@k`, beside the existing author entropy. Then a harness comparing `civic` against the popularity baseline on one pinned corpus.

**S7 — Validation, before any real-data number is interpreted.**
1. **Synthetic ground truth** — `testing.dummy_corpus` already plants a\*/b\* author communities; generate like events with known cross-community bridging posts and assert `civic` ranks them above equally-popular one-sided posts.
2. **Circularity check** — run S3 with and without the S2 fit-set exclusion. A large in-sample improvement is a specification artifact and is the reason the split exists.
3. **Small-audience sensitivity** — bootstrap the balance estimate over engagers; set `--min-engagers` from that curve rather than from taste.
4. **Parameter sweep** — dim, min-likes, shrinkage, weights, to establish the ranking is not an artifact of one arbitrary setting.

**S8 — Output.** Scripts, intermediate artifacts, plots and findings under this folder; root `.md` files updated with a dated line.

### A.3 Deliberately out of scope

No change to corpus pinning (new features read raw events directly, as `likes_spectral` does). No real toxicity/sentiment model in v1. No request-time rerank — `civic` needs corpus-global aggregates and text, which the SQL candidate query cannot express, so it belongs in the batch producer seam.

---

## Variant B — discrete communities from the follower graph

> *"Run community detection on the follower graph. This allows us to filter posts that have diverse approval (e.g., through likes) from people belonging to different communities. Possible issues: language-specific communities? Implementation: compute the communities once and keep track of changes to re-run the detection. Also look into algorithms where we don't have to update the whole detection."*

The substantive difference from Variant A: **the grouping comes from a different modality than the outcome.** A infers viewpoint from likes and then measures like-diversity — the circularity that forces sample-splitting. B infers community from *follows* and measures diversity of *likes*, so the estimator is not fitted on the quantity it scores. That is a genuine structural advantage, and it is the reason to take B seriously despite the higher ingestion cost.

### B.0 The data problem comes first

**The follower graph is not ingested today.** Two separate blockers, both verified:

1. `app.bsky.graph.follow` is not in `DEFAULT_COLLECTIONS` ([jetstream.py:52-56](../packages/data-engineering/src/data_engineering/ingest/jetstream.py#L52-L56)), so Jetstream filters follows out server-side.
2. Even when requested, `parse_event` would discard the edge. A follow record's `subject` is a **bare DID string**, not a `{"uri": ...}` object, and the parser only reads `record["subject"]["uri"]` for likes and reposts. Verified: a synthetic follow event parses to `subject_uri: null`, `subject_author: null` — the followee is lost.

Both are small changes (one list entry, one parser branch), but there is a deeper issue that no parser change fixes: **a firehose slice yields new follows created in-window — a flow, not the standing stock.** Community detection needs the standing graph.

**Backfill path, verified 2026-09-02:** `app.bsky.graph.getFollows` and `app.bsky.graph.getFollowers` on `public.api.bsky.app` both answer **unauthenticated** with cursor pagination (tested against `did:plc:z72i7hdynmk6r22z27h6tvur`; 10 and 5 rows returned, cursor present). `ingest/fetch.py` already talks to that host, so the crawler follows an established pattern.

**Crawl scope.** Crawl follows only for the corpus's **user universe** — the `users/<stem>` artifact `corpus-from-slice` already pins. At the current corpus that is ~2,560 engagers, so ~2,560 cursor-paginated crawls. New CLI `crawl-follows`, parameterized on concurrency, per-user page cap, and rate limit; output an immutable artifact `raw/graph/follows-<stamp>` with its own catalog ref, so detection reruns never re-crawl. This is the long pole of Variant B.

**Which graph to detect on** — two options:

| option | edges | trade-off |
|---|---|---|
| induced subgraph on the universe | engager → engager follows only | small and cheap, but likely fragments: most engagers do not follow each other |
| **co-follow projection (recommended)** | engagers × followees, projected to engager–engager similarity | mirrors the co-like logic already in the repo, stays dense, and needs no crawl of the followees themselves |

### B.1 Community detection

No graph library is installed (`networkx`, `igraph`, `leidenalg` all absent; `scikit-learn` 1.9.0 and `scipy` are present). Options, behind one owner module so the choice stays a parameter rather than a rewrite:

| algorithm | dependency | resolution control | warm start | note |
|---|---|---|---|---|
| **Leiden (recommended)** | `igraph` + `leidenalg` (new) | γ parameter | **yes** — accepts an initial membership | best quality; the warm start is exactly what B.4 needs |
| Louvain | `networkx` (new, lighter) | γ parameter | no API for it | fallback if the extra deps are unwelcome |
| Label propagation | none (≈30 lines over a CSR matrix) | none | trivially | cheap incremental path, but unstable and resolution-blind |
| Spectral clustering | none (`scipy.sparse.linalg`, already used) | explicit *k* | no | reuses machinery the repo already has; forces you to name *k* |

γ (or *k*) sets how many communities exist and therefore how easy "diverse approval" is to earn. **It must be swept, not fixed.** Users with no crawled follows get no label and take the DEFAULT — which must contribute *nothing*, never form an implicit extra community.

### B.2 Features

Two artifacts, one owner module, following the existing `likes_spectral` / `likes_spectral_users` precedent of one decomposition producing two feature sets:

- **`community`** (user-keyed) → `{community_id, community_size, follow_degree, resolution}`.
- **`community_profile`** (diagnostic, community-keyed) → per community: size, dominant post language, top authors, share of corpus engagement. This is the artifact that *answers* the language question instead of assuming it away.

Then the post-keyed diversity feature, deliberately a **separate feature name** from Variant A's rather than a mode flag on it — one owner per feature, and it lets the eval compare the two head to head:

- **`audience_diversity_community`** (post-keyed) → over the likers of *p* mapped to labels, with shares `q_c`:
  `{n_likers, n_labeled, n_communities, entropy, effective_communities, second_share, entropy_excess}`.

`second_share` — the share held by the runner-up community — is the most direct operationalization of "approval from people belonging to different communities", and is far more legible than entropy when explaining a ranking.

### B.3 The estimator trap: entropy is biased at small audiences

The median corpus post has on the order of 7–17 likers. Plug-in entropy is **biased downward** at small sample sizes, so a raw entropy ranking would partly rank by audience *size* — a popularity ranking wearing a diversity costume, which is precisely the failure mode this project exists to avoid. Three mitigations, all in the plan:

1. A bias correction (Chao–Shen or Miller–Madow) on the entropy estimate.
2. `--min-likers` abstention: below the threshold the feature returns its DEFAULT rather than a number.
3. **Report the excess over a null**: expected entropy when the same number of likers is drawn from the corpus-wide community distribution. `entropy_excess = observed − null` makes small- and large-audience posts comparable, and is the quantity the ranker should consume.

### B.4 The language confound

The concern is well-founded: on Bluesky, follow-graph communities track language and country strongly, so an uncorrected "diverse approval" score will reward *multilingual* reach and call it *ideological* bridging. Three-part answer:

1. **Diagnose, don't assume.** `community_profile` reports each community's language distribution. Note a gap: `corpus-from-slice` hardcodes `langs: []` ([slice_corpus.py:75](../packages/data-engineering/src/data_engineering/ingest/slice_corpus.py#L75)), so language must come from the raw post record (`record["langs"]` is available at parse time but not currently projected) or from a detector on post text. Closing that gap is a prerequisite step, not an afterthought.
2. **Condition on language.** Measure a post's audience diversity against the community distribution of users who engage *in that same language*, so "diverse" cannot be earned by merely crossing a language boundary.
3. **Optionally detect within-language.** Run detection on a language-restricted subgraph, making multilingual reach an explicit separate axis rather than a contaminant.

The diagnostic in (1) must run and be read *before* any ranking numbers are interpreted. If communities turn out to be near-perfectly language-aligned, the whole variant reduces to a multilingualism score and should be reported as such.

### B.5 Recomputation and incrementality

**Versioning.** Each detection run is an immutable artifact with its own `MODEL_VERSION`, exactly like every other feature. Consumers pin a version and are unaffected by a later re-detection.

**Trigger, not a clock.** Track churn since the last run — new follow edges, new nodes, edge turnover — and re-run when it crosses a parameterized threshold. This matches the repo's stated "adopt on trigger" discipline and avoids re-running on a schedule that has nothing to do with whether the graph moved.

**Warm start.** Leiden and Louvain both accept an initial membership; seeding with the previous partition and running only local moving is much cheaper than a cold run *and* keeps consecutive partitions similar. `leidenalg` exposes this directly (`initial_membership`), which is the strongest single argument for that dependency.

**Label stability is the hidden requirement.** Community IDs are arbitrary and permute between runs. Without a matching step, a stored `community_id` silently changes meaning between detections and every downstream feature value is quietly corrupted — the `MODEL_VERSION` contract would be a lie. So: match new communities to previous IDs by Jaccard overlap (Hungarian assignment), and record births, deaths, merges and splits in the artifact manifest. This is not optional polish; it is what makes the feature reusable across runs.

**Genuinely incremental algorithms**, for when a cold run stops being affordable: incremental/dynamic Louvain (DynaMo and successors), streaming label propagation, and the simplest practical variant — re-run local moving only for nodes touched by new edges plus their immediate neighborhoods, leaving the rest of the partition fixed.

**Cost anchor:** the like-graph SVD over 24,532 × 51,998 runs in 1.6 s. At current scale a cold re-detection is cheap, and the crawl dominates by orders of magnitude. Incrementality is therefore a **scale-out concern to design the artifact contract for now and implement later** — the versioning, churn trigger and label matching are worth building immediately; a dynamic detection algorithm is not.

### B.6 Steps

1. **B-S1** — parser + collection change so follows survive ingestion (`app.bsky.graph.follow`, `record["subject"]` as a bare DID); project `langs` while in there. Small, tested, isolated.
2. **B-S2** — `crawl-follows` CLI over the pinned `users/<stem>` universe → `raw/graph/follows-<stamp>`. Parameterized concurrency, cap, rate limit; resumable.
3. **B-S3** — `community` + `community_profile` features from the co-follow projection; algorithm and γ as parameters; label matching against the previous run.
4. **B-S4** — language diagnostic read-out. **Gate:** if communities are ≈ language partitions, stop and re-scope before proceeding.
5. **B-S5** — `audience_diversity_community` feature with bias correction, abstention threshold, and null-excess.
6. **B-S6** — the `civic` ranker consumes it in place of (or alongside) the spectral variant; eval compares both on one pinned corpus.
7. **B-S7** — validation: planted communities in synthetic data; agreement analysis between B's discrete labels and A's continuous axis (do they recover the same split?); churn/stability measurement across two detections.

---

## Comparing the two variants

| | **A — spectral axis (likes)** | **B — communities (follows)** |
|---|---|---|
| data needed | already on disk | new parser branch + a ~2,560-user crawl |
| circularity | grouping fitted on the same modality it scores; **needs sample-splitting** | grouping from follows, outcome from likes — **structurally separate** |
| output | continuous axis, dispersion/balance | discrete labels, share/entropy/`second_share` |
| interpretability | a latent axis with no validated meaning | "liked by people in ≥2 communities" — legible, explainable to a user |
| main risk | the axis may not be ideology at all | communities may be language, not ideology (B.4) |
| free parameters | dim, min-likes, shrinkage | γ / k, min-likers, matching threshold, crawl depth |
| stability across runs | vectors rotate; sign must be pinned | labels permute; needs explicit matching (B.5) |
| time to first number | ~a day | crawl-dominated, several days |

**Recommendation:** build A first because the data is in hand, but define `audience_diversity_*` as a *family* of post-keyed features with a common value contract so B drops in behind the same ranker and the same eval. Then compare them on one pinned corpus. B's separation of modalities is the stronger design; A is the faster way to find out whether the rest of the pipeline — tone, weights, evaluation — behaves at all. Neither should be interpreted before its validation step (A.S7 / B-S7) has run.

---

## Open decisions

1. **Fit set for viewpoints (A).** Exclude corpus posts (recommended: one slice, 72% engager coverage) vs. a disjoint earlier window (cleaner, needs a second slice).
2. **What counts as "audience".** Likes + reposts (recommended: unambiguously affirmative) vs. adding repliers (more coverage, but a hostile reply would double-count against the tone feature).
3. **Slice length for A.S1.** 3 hours (recommended) vs. longer; ~430 MB/hour of raw events at the observed rate.
4. **Variant B dependencies.** Accept `igraph` + `leidenalg` for warm-startable Leiden (recommended), or stay dependency-free with label propagation / spectral clustering and give up the warm start.
5. **Order of work.** A first then B (recommended), both in parallel, or B only — accepting the crawl as the critical path.
