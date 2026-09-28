# For-you feed — a two-stage pipeline (retrieval → ranking) over an engagement snapshot

**Date:** 2026-09-03
**Status:** design only. Nothing implemented, nothing run. No number in this document is a
measurement; every quantity below is a *specification*.
**Prior art in this repo:** [repo learnings](../2026-09-02-repo-learnings/2026-09-02-repo-learnings.md)
(architecture, feature contracts, ranker seams) and the
[bridging-ranker plan](../2026-09-02-bridging-ranker/2026-09-02-bridging-ranker-plan.md)
(Variant A: viewpoint axis from likes; Variant B: communities from follows). This document
supersedes neither — it places both inside a full retrieval + ranking pipeline and fixes the
estimator choices that were left open there.

---

## 0. The specification, restated

Inputs, as given:

| input | cadence | what it is here |
|---|---|---|
| **engagement snapshot** | per batch (minutes–hours) | event rows `(did, collection, uri, subject_uri, subject_author, created_at, text)` — likes, reposts, posts, replies |
| **follower network** | stable, refreshed on trigger | directed follow edges `u → v` |
| **user metadata** | stable | declared language, account age, labels/flags, handle |

Required behaviour:

1. **Retrieval** surfaces top-*k* relevant posts by standard CF, **and** posts with *diverse
   approval*.
2. **Ranking** prioritizes diverse-approved posts and demotes content generating negative or
   low-quality interactions.
3. Content features (text, URL) are allowed, **preferably not in retrieval**.

Three consequences drive every choice below, and each is a design commitment rather than a
preference:

- **Retrieval must be indexable.** Anything that decides the candidate set has to be either a
  per-viewer vector lookup or a precomputed post-keyed scalar that a database can `ORDER BY`.
  That rules text scoring out of retrieval on cost grounds alone, which happens to match the
  stated preference. Text enters at ranking, over ≤10³ candidates.
- **"Diverse approval" is estimated on a small sample.** The median post has on the order of
  10 approvers. Any diversity statistic computed plug-in from 10 draws is biased, and the bias
  shrinks with audience size — so an uncorrected diversity ranking is *partly a popularity
  ranking*. §3.3 fixes this with an unbiased estimator plus a null model, and this is the
  single most important calculation in the pipeline.
- **The grouping and the outcome must come from different modalities.** Communities come from
  **follows**; approval diversity is measured over **likes**. Estimating a user's group from
  likes and then scoring like-diversity is circular and needs sample-splitting to mean anything
  (see §3.1's fallback and §8's gate G2).

Out of scope, deliberately: impression/exposure logging (so no trained pCTR — §5.4), a real
toxicity model (heuristic stand-in, same output range), notification/social-proof features,
multi-objective learning-to-rank.

---

## 1. Notation

| symbol | meaning |
|---|---|
| `u, v` | users (DIDs) · `p` | posts (`at://` URIs) |
| `W` | the snapshot window; `P_W` the posts eligible to be ranked; `U_W` the users active in it |
| `L = {(u,p)}` | like edges in `W`, deduplicated · `R = {(u,p)}` reposts · `Y = {(u,p)}` replies |
| `E⁺(p)` | **approvers** of `p` = likers ∪ reposters (affirmative only; repliers excluded — a reply is not approval) |
| `n(p) = \|E⁺(p)\|` | approver count |
| `F` | follow graph, `N(u)` = accounts `u` follows |
| `c(u) ∈ {1..C}` | community label of `u`; `m_u ∈ Δ^{C}` the (possibly soft) membership vector |
| `π ∈ Δ^{C}` | corpus-wide community distribution of approvals — the null |
| `z_u, z_p ∈ ℝ^d` | CF vectors in **one shared space** (`likes_spectral`) |
| `f_u ∈ ℝ^{d'}` | follow-graph viewpoint vector (continuous alternative to `c(u)`) |
| `q(p) ∈ Δ^{C}` | community shares of `p`'s approvers, `q_c(p) = n_c(p)/n(p)` |
| `r(p) ∈ Δ^{C}` | community shares of `p`'s **repliers** |

---

## 2. The pipeline

```
STABLE (refreshed on trigger)                 SNAPSHOT (refreshed per batch)
─────────────────────────────                 ──────────────────────────────
follow graph F        user metadata M         engagement events (likes/reposts/replies/posts)
      │                     │                              │
      ▼                     │                              ▼
 O1 co-follow projection    │                    O2 like-graph CF (SVD)
    + community detection   │                       z_u , z_p   [shared space]
    c(u) / m_u , f_u        │                              │
      └──────────┬──────────┘                              │
                 ▼                                         │
        O3 approval aggregation  ◄───── approvers E⁺(p) ────┤
           q(p), D̂(p), excess, z_D(p)                       │
                 │                                         │
        O4 response-quality aggregation ◄── replies Y ──────┘
           neg_rate, ratio, JS(q‖r)
                 │
   ══════════════╪═══════════════════════════════════════════════════
   RETRIEVAL     ▼   (viewer u, per request or per batch)
      R1 CF top-K_cf         ⟨z_u , z_p⟩              personalized, indexed (ANN)
      R2 bridging top-K_br   z_D(p) fresh-gated       viewer-independent, cacheable
      R3 in-network K_net    approved by N(u)         cold-start cover, high precision
      R4 exploration K_exp   fresh, under-measured    breaks the feedback loop
                 │
          union → dedupe → hard filters (seen, self, blocked, author cap)
                 │  candidate set S(u), |S| ≈ 500–1000
   ══════════════╪═══════════════════════════════════════════════════
   RANKING       ▼
      normalize every component to within-S percentile
      S(u,p) = w_rel·r̃ + w_br·b̃ + w_x·x̃ + w_fresh·φ − w_neg·ñ
      hard-demote bucket (HaR bin ≥ τ_har  or  neg_rate ≥ τ_neg)
      MMR diversification over author + community
                 ▼
              top-k feed
```

Everything above the double line is viewer-agnostic and batch-computed; everything below is
cheap and viewer-conditioned. That is the same split the repo already enforces (batch fits,
serving applies), so the pipeline drops onto the existing store rather than beside it.

---

## 3. Offline blocks — the calculations

### O1 — Communities and viewpoints from the follow graph

**Why follows and not likes.** The diversity outcome is measured over likes. If the grouping
were also fitted on likes, a post's "audience diversity" would be scored by a model fitted on
the very edges forming that audience. Follows are the second modality that removes the
circularity structurally instead of by sample-splitting.

**Step 1 — projection.** The induced engager→engager follow subgraph is sparse (most engagers
do not follow each other) and fragments. Use the **co-follow projection** instead: `B` the
binary `|U_W| × |followees|` matrix, and

```
S_uv = |N(u) ∩ N(v)| / sqrt(|N(u)| · |N(v)|)          (cosine on followee sets)
```

computed as `Ŝ = B̂ B̂ᵀ` with `B̂ = D_u^{-1/2} B`. Two controls keep it sparse and meaningful:

- **IDF-damp hub followees**: weight followee `w` by `1/log(1 + deg(w))`, so following a
  celebrity everyone follows carries little similarity.
- **kNN sparsification**: keep the top `m` neighbours per node (`m ≈ 30`), symmetrize by
  `max`. Without this, `Ŝ` is dense and detection is quadratic.

**Step 2 — detection.** Leiden at resolution `γ` → hard labels `c(u)`, sizes `|C_c|`.
`γ` sets how many communities exist and therefore how easy "diverse approval" is to earn: it
**must be swept, not fixed** (§7). Users with no crawled follows get *no label* and contribute
nothing — they must never form an implicit extra community, which would manufacture diversity
out of missing data.

**Step 3 — labels must be stable across runs.** Community IDs permute between detections. Match
new communities to old by Jaccard overlap under Hungarian assignment; record births/deaths/
merges/splits in the artifact manifest. Without this, a stored `community_id` silently changes
meaning and every downstream number is quietly corrupted.

**Continuous alternative, same modality.** Spectral embedding of the same `Ŝ` gives `f_u ∈ ℝ^{d'}`
and a soft membership `m_u` (softmax over distance to community centroids, temperature `T`).
Soft membership matters in §5.2, where a viewer's own position enters the score.

**Fallback when the follow crawl is unavailable.** Use `likes_spectral_users` for `f_u`, fitted
on a **disjoint edge set** — likes outside `P_W`, or an earlier window — and record this in the
artifact so the eval knows the estimator is the weaker one. This is Variant A of the prior plan,
kept as a fallback rather than the default.

### O2 — Collaborative filtering on the like graph

Unchanged from what the repo already computes ([likes_spectral.py](../packages/data-engineering/src/data_engineering/features/likes_spectral.py)),
stated here for completeness because retrieval depends on its exact properties:

```
A       users × posts, binary, one edge per (u,p)
Â       = D_u^{-1/2} A D_p^{-1/2}                       degree-normalized
Â'      = Â − u₀v₀ᵀ,   u₀ = sqrt(d_u/L), v₀ = sqrt(d_p/L)   trivial triplet deflated exactly
Â' ≈ UΣVᵀ                                                truncated at d, largest component only
z_p     = D_p^{-1/2} V Σ        z_u = D_u^{-1/2} U Σ      one shared space
rel(u,p) = ⟨z_u , z_p⟩
```

Three properties constrain how it may be used:

1. Only the **largest connected component** is embedded; everyone else takes the zero vector,
   whose every dot product is 0 — meaning *"no information"*, not *"neutral taste"*. In a stable
   sort a zero score leaves the incoming order alone, which is the correct fallback.
2. Precision scales with degree. A user with one like has a vector saying little more than
   "liked that one post". Any aggregate over users should weight or threshold on degree.
3. **Fresh posts are invisible to it.** A post minutes old has `n(p) ≈ 0` and no usable `z_p`.
   Retrieval sources R3 and R4 exist precisely to cover this; without them the pipeline can only
   recommend posts that are already popular, which is a feedback loop, not a recommender.

**Author prior for cold posts.** `z̃_p = (n(p)·z_p + κ_a·z̄_{author(p)}) / (n(p) + κ_a)`, where
`z̄_{author}` is the degree-weighted mean of the author's embedded posts. At `n(p)=0` the post
inherits its author's position; the prior washes out as evidence arrives. `κ_a` parameterized.

### O3 — Approval aggregation: what "diverse approval" *is*

Per post `p`, over approvers `E⁺(p)` that carry a community label (count `n_ℓ(p)`):

```
q_c(p) = n_c(p) / n_ℓ(p)
```

**The estimator problem.** The obvious statistic — Shannon entropy `H(q)` — is biased downward
at small `n`, and the bias depends on `n`. Ranking by raw `H` therefore ranks partly by audience
size: a popularity ranking wearing a diversity costume, which is the exact failure mode this
project exists to avoid.

**Primary statistic — unbiased Gini–Simpson.** For approvers modelled as `n_ℓ` draws from `p`'s
audience distribution, the plug-in `1 − Σ q_c²` is biased, but its U-statistic form is
**exactly unbiased**:

```
D̂(p) = 1 − Σ_c  n_c(p)·(n_c(p) − 1)                     defined for n_ℓ(p) ≥ 2
                 ─────────────────────
                 n_ℓ(p)·(n_ℓ(p) − 1)
```

`D̂(p)` = the probability that two distinct approvers drawn without replacement belong to
different communities. It is unbiased at every `n_ℓ ≥ 2`, it needs no bias correction, and it is
directly interpretable to a human — which is why it is the primary statistic and entropy is
demoted to a reported secondary.

**Null model.** `D̂` is unbiased for *this post's* audience diversity, but the corpus itself has a
background diversity: with `π` the corpus-wide approval share by community,

```
D_null = 1 − Σ_c π_c²                       (no n-dependence)
excess(p) = D̂(p) − D_null
```

`excess` is what the ranker consumes: it answers "is this post's audience more mixed than a
random audience of the same corpus", not "is this post popular".

**Standardization.** `D̂` has variance decreasing in `n_ℓ`, so at fixed `excess` a 200-approver
post is far better evidenced than a 5-approver one. Convert to a z-score:

```
z_D(p) = excess(p) / sd(p),      sd(p) estimated by bootstrap over approvers (B resamples)
                                 or by the U-statistic variance, whichever is validated first
```

Ranking on `z_D` deliberately reintroduces a mild preference for well-measured (larger) audiences
— but as *confidence*, not as popularity, and the two are separable in the eval (§8, gate G3).

**Legible companions**, emitted alongside because `z_D` is not explainable to a user:

```
second_share s₂(p) = second-largest q_c(p)          "≥2 communities really liked this"
pair_coverage(p)   = #{(c,c') : n_c ≥ 1, n_{c'} ≥ 1} / C(C,2)
effective_C(p)     = 1 / Σ_c q_c(p)²                inverse Simpson
```

**Independence correction (why raw approver counts overstate diversity).** Approvers are not iid:
a post can be liked by five members of one tight follow-clique. Down-weight correlated approvers
by their follow-neighbourhood overlap — approver `u` gets weight
`ω_u = 1 / (1 + |{v ∈ E⁺(p) : v ∈ N(u) ∪ N⁻(u)}|)`, and `n_c` becomes `Σ ω_u`. Parameterized on/off;
it is a refinement, and the pipeline must be validated with it **off** first so its effect is
attributable.

**Abstention and defaults.** Below `n_ℓ(p) < n_min` (default 5) the feature returns its `DEFAULT`
= the corpus-mean excess (i.e. contributes nothing), never the maximum. `n_ℓ` and `n(p)` travel
with the value so every consumer can gate on coverage.

**The language confound must be resolved before any of this is interpreted.** Follow-graph
communities on Bluesky track language and country strongly, so an uncorrected diverse-approval
score can reward *multilingual reach* and call it *bridging*. Three-part answer, in order:
(i) diagnose — emit a per-community language profile; (ii) condition — compute `π` and `q` within
the post's own language stratum, so crossing a language boundary cannot by itself earn diversity;
(iii) optionally detect communities within-language, making multilingual reach an explicit
separate axis. Gate G1 in §8 stops the pipeline here if communities turn out to be ≈ language
partitions.

### O4 — Negative and low-quality response aggregation

Approval diversity says nothing about *how* people responded. Four post-keyed quantities, all
from the snapshot except the last:

```
reply_rate(p)  = |Y(p)| / (n(p) + 1)                    "ratio'd" proxy; high = contested
neg_rate(p)    = share of replies with valence < 0       lexicon v1, swappable for a classifier
mean_valence(p)= mean reply valence ∈ [−1, 1]
JS_div(p)      = JS( q(p) ‖ r(p) )                       approval vs reply community divergence
```

`JS_div` is the discriminating one and is the reason repliers are excluded from `E⁺`. Two posts
can have identical approval diversity:

- a **bridge**: approvals spread across communities, replies drawn from the same spread →
  `JS_div` low.
- a **flame**: approvals concentrated in community A, replies pouring in from B →
  `JS_div` high, `neg_rate` high. Cross-community *attention* is not cross-community *approval*,
  and without `JS_div` the pipeline cannot tell them apart.

Content-derived, at ranking only: `har(p)` (likelihood of attracting toxic replies) and `ar(p)`
(affective intensity), already present as deterministic stand-ins in
[baseline/scorers.py](../packages/baseline/src/baseline/scorers.py) with the same output ranges as
the models that will replace them.

**Coverage warning, from the measured slice:** at a 10-minute window only 26 corpus posts carried
≥3 replies. Every reply-derived quantity above is unusable at that window length; it needs hours,
and `--min-replies` must make the feature abstain rather than report noise as tone.

---

## 4. Retrieval

Four sources with independent quotas, unioned. Quotas are parameters, not constants, and each
source answers a different failure of the others.

**R1 — CF relevance, `K_cf`.** `argmax_p ⟨z_u , z_p⟩` over posts with `n(p) ≥ n_cf`.
Exact scan is fine at ≤10⁴ posts (500 candidates × 16 dims is microseconds); above that, an ANN
index over `z_p` (IVF/HNSW; pgvector when the store moves to Postgres). Viewer with no embedding →
zero vector → this source returns nothing, and R3/R4 carry the request.

**R2 — Diverse approval, `K_br`.** `ORDER BY z_D(p) DESC` among posts passing
`n_ℓ(p) ≥ n_min ∧ age(p) ≤ age_max`. **Viewer-independent**, so it is one cached list serving all
requests — which is what makes bridging retrieval affordable. This is exactly the shape the repo's
`top_by_feature(feature, field=...)` already serves, so `z_D` must be a numeric field with a
SQL-safe name inside the feature value JSON.

**R3 — In-network, `K_net`.** Posts approved by accounts the viewer follows, ordered by
`Σ_{v ∈ N(u) ∩ E⁺(p)} ω_v` with `ω_v` a follow-affinity weight. Cheap, high precision, and the
only source that works on day one for a new user. Cap contribution per followed account so one
prolific friend cannot fill the feed.

**R4 — Exploration, `K_exp`.** Fresh posts with `n(p) < n_min` — the ones every other source is
blind to — sampled with probability `∝ 1/(1 + n(p))` within a recency window, optionally stratified
by author community so exploration itself is not one-sided. Small quota (5–10% of the set). Without
R4 the system only ever learns about posts it already promoted.

**Union and hard filters**, applied after dedup by post URI:

```
drop  already-seen (viewer × post, TTL'd)      drop  viewer's own posts
drop  authors the viewer blocks/mutes          drop  language ∉ viewer's declared languages (metadata)
cap   ≤ a_max posts per author                 cap   |S(u)| = 500–1000
```

Filters are hard because they encode user intent, not preference; nothing downstream can outweigh
them. Each source's contribution is tagged on the candidate (`source: cf|br|net|exp`) — that tag is
what makes it possible to measure *which source's posts survive ranking*, which is the only
honest way to tune the quotas (§8).

---

## 5. Ranking

### 5.1 Normalization — the precondition for adding anything up

The components live on incomparable scales: `⟨z_u,z_p⟩` is an unbounded dot product, `z_D` a
z-score, `neg_rate` a proportion. Summing them raw makes the weights meaningless and lets one
heavy-tailed component dominate. So **every component is mapped to its within-candidate-set rank
percentile** before weighting:

```
x̃(p) = rank of x(p) within S(u), ties averaged, divided by |S(u)|   ∈ (0,1]
```

This is the same average-rank ECDF the `popularity` extractor already uses, applied per request.
It makes weights interpretable as trade-off rates, is robust to outliers, and is scale-free.

**On "recalculate within the candidate set?"** — the specification offered both. The distinction
worth keeping: `D̂`, `excess` and `z_D` are **post-local aggregates** over that post's approvers;
restricting to the candidate set does not change them, it only shrinks the sample used for the
null `π`, making it noisier and viewer-dependent. So: **estimate globally, normalize locally**.
The one quantity that genuinely must be computed within the set is any diversity of the *feed*
itself (§5.5), which is a property of the selection, not of a post.

### 5.2 The score

```
S(u,p) = w_rel · r̃(u,p)              relevance      pct of ⟨z_u , z_p⟩
       + w_br  · b̃(p)                bridging       pct of z_D(p)
       + w_x   · x̃(u,p)              cross-exposure pct of cross(u,p)
       + w_fr  · φ(p)                freshness      exp(−Δt / τ)
       − w_neg · ñ(p)                quality        pct of neg(p)
```

with

```
cross(u,p) = 1 − Σ_c m_{u,c} · q_c(p)      P(a random approver is not from the viewer's community)
neg(p)     = α₁·neg_rate + α₂·reply_rate + α₃·JS_div + α₄·har        α's parameterized
```

`cross(u,p)` is what makes the feed bridging **for this viewer** rather than globally: `b̃` says
"this post reached across communities", `x̃` says "one of the sides it reached is not yours". A
feed optimizing `b̃` alone can still hand every viewer the posts their own community approved of,
as long as someone else also approved. `m_u` soft membership matters here — a hard label would
make `cross` a step function of an arbitrary partition boundary.

### 5.3 The demotion bucket — a gate, not a weight

Weighted sums let a very popular toxic post buy its way past a penalty. Quality is therefore
lexicographic, mirroring the baseline ranker's HaR demotion:

```
if  har_bin(p) ≥ τ_har   or   neg_rate(p) ≥ τ_neg   →   demoted bucket
ordered list = sort(kept, by S desc)  ++  sort(demoted, by S desc)
```

Demotion, not removal: the pipeline's quality signals are heuristics standing in for models, and a
heuristic should not have deletion authority. Removal is a moderation decision with a different
evidentiary bar; the ranker's job stops at ordering.

### 5.4 Why v1 is a weighted sum and not a learned model

The principled form is `S = P(positive engagement | u,p) × bridging multiplier`, with the
probability trained on the snapshot. It is not available yet, and the blocker is data, not effort:
**there are no impressions**. Without "who could have seen it", a like/no-like label has no
denominator, and a model fitted on it learns exposure, not preference. So v1 is a transparent
weighted sum with swept weights, and the exposure model is the prerequisite for v2 — matching the
research agenda's R2 "exposure model" item, which is on the critical path for learned ranking and
not merely adjacent to it.

### 5.5 Diversification of the final list

Post-level scores do not prevent a top-10 of ten near-identical posts. Greedy MMR over the ranked
list:

```
select p* = argmax_p [ S(u,p) − λ · max_{p' ∈ selected} sim(p, p') ]
sim(p,p') = cos(z_p , z_{p'})   plus a fixed penalty when author or author-community repeats
```

with per-community share caps on the emitted top-`k`. `λ` and the caps are parameters; the caps are
what stop a "bridging" feed from being a feed *about* one community's fight.

---

## 6. Artifact and code mapping

New feature artifacts, following the repo's one-owner-module contract
(`FEATURE`, `MODEL_VERSION`, `KEYED_BY`, `DEFAULT`, `REQUEST_SAFE`, pure `compute()`):

| artifact | keyed by | value | `DEFAULT` |
|---|---|---|---|
| `community` | user | `{community_id, membership[], community_size, follow_degree, resolution}` | `{community_id: null}` → contributes nothing |
| `community_profile` | community | `{size, lang_dist, top_authors, share_of_approvals}` | diagnostic only |
| `approval_diversity` | post | `{n, n_labeled, shares[], D_hat, excess, z_D, second_share, effective_C}` | corpus-mean excess, `z_D = 0` |
| `response_quality` | post | `{n_replies, neg_rate, mean_valence, reply_rate, js_div}` | neutral: `neg_rate = corpus mean` |
| `likes_spectral{,_users}` | post/user | existing | existing |
| `popularity` | post | existing | existing |

New raw zone: `raw/graph/follows-<stamp>` from a `crawl-follows` CLI over the pinned user universe.
**This is the long pole** — follows are not ingested today (not in `DEFAULT_COLLECTIONS`, and
`parse_event` reads `record["subject"]["uri"]` while a follow's subject is a bare DID), and a
firehose slice gives new follows *created* in-window, a flow rather than the standing stock the
detection needs. The public AppView answers `getFollows`/`getFollowers` unauthenticated with cursor
pagination, so the backfill path exists; it just has to be built.

Seams touched, in the repo's terms:

- **Retrieval R2** is `top_by_feature("approval_diversity", field="z_D")` — the cheapest
  intervention in the system, and it needs `z_D` to satisfy the store's `^[A-Za-z_][A-Za-z0-9_]*$`
  field-name guard.
- **Retrieval R1/R3 and the whole ranker** are a new batch producer emitting per-viewer feed docs
  (`{algo, version, corpus, viewer, feed:[{post_id, score}]}`), published with
  `publish-serving --feed` and served under its own rkey. Nothing new in serving code.
- **Request-time** stays the existing carve-out: a viewer-conditioned rerank of published
  candidates using `REQUEST_SAFE` arithmetic. Note `Store.lookup` currently issues one SQL
  round-trip per key (≈501 per `cf` request); batching it to `IN (...)` is the obvious fix before
  the candidate set or the feature count grows.

---

## 7. Parameters (nothing hard-coded)

| stage | parameter | role | starting point |
|---|---|---|---|
| O1 | `γ` resolution, `m` kNN degree, IDF damping on/off | how many communities exist ⇒ how easy diversity is to earn | swept, never fixed |
| O1 | `T` softmax temperature | hardness of `m_u` | swept |
| O2 | `d` dims, `min_likes`, `κ_a` author prior | CF capacity and cold-post shrinkage | `d=16`, `κ_a` swept |
| O3 | `n_min` abstention, `B` bootstrap, independence correction on/off, language stratification on/off | estimator validity | `n_min=5`, `B=200` |
| O4 | `min_replies`, lexicon vs classifier | tone validity | `min_replies=3` |
| R | `K_cf, K_br, K_net, K_exp`, `a_max`, `age_max` | the mix entering ranking | tuned on source-survival (§8) |
| Rank | `w_rel, w_br, w_x, w_fr, w_neg`, `α₁..α₄`, `τ`, `τ_har`, `τ_neg`, `λ`, community caps | the trade-off | swept |

---

## 8. Validation, and the gates that precede any interpretation

No ranking number means anything until these pass, in order.

**G1 — Language.** Read `community_profile`. If communities are ≈ language partitions, the
diverse-approval score is a multilingualism score; stop, apply within-language stratification, and
re-report. Do not proceed on a hope that it will wash out.

**G2 — Circularity.** Recompute `approval_diversity` with communities from *likes* instead of
follows. A large improvement in the likes-fitted version is a specification artifact, and its
magnitude is the measurement of how much the modality separation is buying.

**G3 — Diversity is not popularity.** Correlate `z_D(p)` with `n(p)` across the corpus, and compare
against the same correlation for uncorrected plug-in entropy. The unbiased estimator plus null
model should visibly break the dependence; if it does not, the estimator is wrong, not the data.

**G4 — Planted recovery.** Generate synthetic events with known bridging posts (the repo's dummy
corpus already plants author communities) and assert the pipeline ranks them above equally-popular
one-sided posts. This is the only test where ground truth exists.

**G5 — Small-audience sensitivity.** Bootstrap `D̂` over approvers across the `n` range; set `n_min`
from where the interval stops spanning the corpus mean, not from taste.

Then, offline evaluation on a **temporal split** — fit communities and CF on `[t₀,t₁)`, evaluate
against engagement in `[t₁,t₂)`:

| metric | what it answers |
|---|---|
| `recall@k` / `nDCG@k` vs held-out likes | did retrieval keep relevance while adding bridging? |
| `audience_diversity@k` = mean `excess` over the emitted feed | is the feed actually diverse-approved? |
| `cross_community_exposure@k` = mean `cross(u,p)` | does the *viewer* see across the boundary? |
| `negative_response@k` = mean `neg_rate`, share demoted | is low-quality content actually down? |
| `author_entropy@k`, max author share (existing) | concentration |
| **source survival** = share of top-`k` per retrieval source | are the quotas earning their keep? |

Source survival is the quota-tuning instrument: a source contributing 30% of candidates and 2% of
the top-`k` is buying nothing.

---

## 9. Build order

1. **B1** — ingest follows: collection + parser branch, then `crawl-follows` over the pinned user
   universe → `raw/graph/follows-<stamp>`. Also project post `langs`, needed for G1. *Blocking for
   everything bridging.*
2. **B2** — `community` + `community_profile`, with label matching across runs. **Run G1.**
3. **B3** — `approval_diversity` with `D̂`, null, `z_D`, abstention. **Run G3, G5.**
4. **B4** — retrieval R1+R2 only; measure recall against held-out likes. First point where the
   pipeline produces a feed.
5. **B5** — `response_quality`; add the demotion bucket and R3/R4. **Run G2, G4.**
6. **B6** — full score with `cross(u,p)`, MMR, weight sweep, source-survival tuning.
7. **B7** — exposure model → learned `P(engage)` for v2 (§5.4).

Each step is one owner module, one artifact, one CLI, so a failure is attributable to a step rather
than to "the pipeline".

---

## 10. Open decisions

1. **Hard labels or soft membership as the primary community representation.** Soft is better for
   `cross(u,p)`; hard is far more legible in explanations and in `second_share`. Recommendation:
   emit both from one detection run, consume hard for `q(p)`, soft for `cross`.
2. **Are reposts approval?** Taken as yes above (affirmative), but quote-posts — currently dropped
   at parse time — are frequently *dis*approval, and folding them in without stance detection would
   corrupt `E⁺`.
3. **Per-viewer batch feeds vs. request-time rerank** for the personalized terms (`r̃`, `x̃`). Batch is
   simpler and matches the existing feed-doc contract; request-time scales better across viewers and
   keeps freshness. The split point is which of `r̃`/`x̃` is cheap enough as a lookup.
4. **Window length.** Reply-derived quality needs hours; CF and diversity need enough likes per post
   to clear `n_min`. One window for both, or separate windows per block with different refresh
   cadences.
5. **Where the feed producer lives** — a new `packages/foryou/`, or inside `evals`, which the docs
   say will own feed generation. Committing to the wrong one costs a move later.
