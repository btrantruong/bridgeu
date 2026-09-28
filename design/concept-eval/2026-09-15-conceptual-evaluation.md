# Evaluation of the BridgEU conceptual plan (`overview.md` + `bridging_ranker_pipeline_v3.pdf`)

**Date:** 2026-09-15
**Stance:** This is an assessment of *specification correctness*, not of merit.
Following the project's analysis rules, no component is called good or bad;
the goal is to determine whether each construct is defined precisely enough to
be implemented and measured on purpose, and to surface the decisions that must
be made *before* any estimate is interpretable. Where the plan already names a
decision as experimental, that is recorded as a path-dependency for the
implementation plans, not a deficiency.

---

## 1. What the plan specifies

A two-stage For-You feed (Fig.1) that treats **personal relevance** and
**bridging** as *distinct retrieval objectives*, unions their candidates, and
ranks the pool by relevance + bridging + recency − harmful-response risk.

- **§2.1 Representation.** Collaborative filtering on positive interactions
  (likes, reposts) → a shared user/post latent space; replies held out as
  ambiguous. Fig.1 writes this as biased matrix factorization
  `r̂_ui = μ + b_u + b_i + p_uᵀ q_i`.
- **§2.2.1 Bridging retriever.** For each post, score how *dispersed* its
  positively-engaged audience is in preference space — rewarding *observed
  convergence* of users with different histories, explicitly **not** geometric
  betweenness — controlling for exposure, popularity, age, and the diversity
  expected at comparable reach; optionally combine with follower-graph
  (network) distance. Top-K → bridging candidates.
- **§2.2.2 Relevance retriever.** Conventional CF top-k by predicted score /
  latent proximity. Top-K → relevance candidates.
- **§2.3 Candidate set.** Union → de-duplicate → topic-diversify (a topic
  model), with an optional *permissive* relevance floor on bridging candidates.
- **§2.4 Ranker.** Score the merged pool by relevance, bridging, recency, and a
  Harmful-Responses (HaR) penalty; start interpretable-weighted, later learned.
- **§3** argues bridging must enter *retrieval*, because heterogeneous-audience
  posts lose the conventional relevance signal and are filtered before a
  bridging-aware ranker can see them; names **overlap(relevance, bridging)** as
  the quantity that justifies the separate pathway.
- **§4** marks the estimators as revisable (CF → two-tower/ANN; graph-embedding
  variants; dispersion measures; topic-conditioning; weighted → learned rank).
- **§5** evaluates deliberative quality, civility/toxicity, audience diversity
  & bridging, and network-level polarization.

## 2. What is well-posed

- **Separating "diverse approval" from "geometric betweenness" (§2.2.1) is the
  correct and non-obvious move.** A post at the midpoint of two camps need
  please neither; requiring *observed* cross-audience approval is the right
  construct and rules out a common failure mode of bridging heuristics.
- **Naming overlap(relevance, bridging) as the decisive empirical quantity
  (§3) makes the central architectural claim falsifiable.** The plan commits to
  the measurement that could show its own extra pathway to be redundant. That is
  the right test and it is cheap to run once both retrievers exist.
- **Holding replies out of the approval signal (§2.1) and re-introducing them
  as a harm signal (§2.4/HaR) is internally consistent** — engagement that may
  encode conflict is not counted as approval, then is used to model conflict.
- **The batch/serving split is already honored by the repo** (expensive fits in
  batch, request-time applies vectors), so the plan's "richer bridging score at
  rank than at retrieval" (§2.4) has a natural home.

## 3. Specification decisions that must be fixed before any estimate is meaningful

These are not tuning knobs; each changes what the number *means*. They are the
core of the experimentation track (§4) and gate the implementation plans.

### 3.1 The diverse-approval estimator is under-determined (highest priority)

§2.2.1 asks for a score that is high when *many* users with *dissimilar*
histories *positively* converge, net of exposure/popularity/age/expected
diversity. Three specification choices are load-bearing and currently open:

1. **Null model.** "Diversity expected among posts with comparable reach" must
   be an explicit null (e.g. audience dispersion under random engagers matched
   on like-count / exposure). The score is then a **residual or z-score against
   that null**, not raw dispersion. Without it the score is confounded by
   popularity — popular posts mechanically gather heterogeneous audiences
   (§2.2.1 says as much). *No null is specified yet.*
2. **Sample-splitting.** The like edges that *fit* the viewpoint embedding must
   not be the same edges whose dispersion is *scored*, or the metric is
   circular (measuring a post's audience spread with vectors trained on that
   very audience). Fit on one time-slice/edge-fold, score on another — or take
   the distance from a different modality (follows). *This is a correctness
   requirement, not an option.*
3. **Evidence weighting.** On a profiled slice, **80 % of engagers cast exactly
   one like** — most per-user viewpoints are barely identified. The
   post-level aggregate must down-weight or threshold on per-user degree
   (shrinkage), or two noisy singletons will outrank consistent cross-audience
   approval — precisely the failure §2.2.1 warns against.

**Consequence:** a diverse-approval point estimate is not interpretable until
(1)–(3) are pinned deliberately. This is the single largest specification risk
in the plan and the first thing the experiments must settle.

### 3.2 Figure ↔ text discrepancy on retriever ranking keys

Fig.1 labels the **bridging** retriever "Ranked by `p_uᵀ q_i`" and the
**relevance** retriever "Ranked by `b_i`". By convention `p_uᵀ q_i` is the
personalized taste match (i.e. *relevance*) and `b_i` is item bias (i.e. global
*popularity*). As drawn, the two labels read as swapped, and neither matches
the §2.2.1 prose, in which bridging is an audience-dispersion score over a
post's engagers (not a user-post dot product at all). The prose should be
treated as authoritative; the figure's rank keys need correcting or explaining.
Flag for the authors — it materially changes what each retriever returns.

### 3.3 Behavioral vs network distance — two constructs, one name

§2.2.1 offers two separations: behavioral (like-graph latent distance) and
network (follower-graph community). The plan wisely keeps them separate "until
evidence supports combining." Implementation must therefore treat them as **two
bridging sub-scores** with an explicit, deferred combination rule, and the
experiments must report each pathway's standalone contribution. Note they have
different coverage and cold-start behavior: behavioral distance is undefined for
the ~single-like majority and for anyone outside the like-graph's largest
component; network distance needs a follower crawl (feasible, §data-sources
Table 1) and different community labels.

### 3.4 Is the bridging candidate list personalized?

A post's cross-audience approval is a property of the *post*, not the viewer, so
the bridging Top-K is largely **viewer-independent** (compute once, share) apart
from §2.3's permissive per-user relevance floor. This is an efficiency
opportunity and a design question the plan leaves implicit: state whether
bridging retrieval is global-then-filtered or genuinely per-user, because it
changes caching, cost, and the meaning of "overlap with relevance" in §3.

### 3.5 Topic handle is a hard dependency with an auth cost

§2.3 topic-diversification and any topic-conditioned representation (§4) need a
**topic model** and a way to *retrieve by topic*. Per the data-source
investigation, unauthenticated topic search is unavailable (`searchPosts`
blocked; Skylight needs a key); topic scoping is either an auth'd session, the
greenearth key, a topical feed, or an expensive firehose keyword filter. This
dependency should be resolved early because two components rest on it.

### 3.6 HaR is a *predictive* model, distinct from the eval toxicity models

§2.4's HaR predicts, *from a post's text before responses exist*, the risk it
will attract harmful replies. §5's toxicity models (toxic-BERT, Perspective,
OpenAI-mod) measure *realized* toxicity of interactions that already happened.
These are different objects and must not be conflated: HaR is a ranking feature
trained to forecast; the §5 models are measurement instruments. The repo's
current `scorers.py` HaR/AR are **deterministic keyword heuristics matched only
to an output range** — placeholders, not models. A Bluesky/European-text-tuned
HaR is its own workstream.

### 3.7 "Negative interaction" appears as a stable objective but has no estimator

§4 lists "negative interaction" among the *stable* objectives alongside
relevance, diverse approval, and interaction quality, yet §2.4 collapses harm
into HaR only. Decide whether realized negative interaction (e.g. observed
hostile replies / blocks / un-likes) is a separate ranking signal or is fully
proxied by predicted HaR. Note the repo drops deletes/un-likes/blocks at ingest
(`parse_event` keeps only create), so any realized-negative signal needs an
ingest change.

## 4. Consistency with the current code base (gap analysis)

The plan and the repo are far apart; the implementation plans exist to close
this. What is genuinely present today versus what the plan needs:

| plan element (`overview.md`) | in repo today | gap |
|---|---|---|
| CF user/post latent space (§2.1) | `likes_spectral` (spectral CF, fixed; **never run on a real slice**) | validate on real data; biased-MF variant of Fig.1 not implemented |
| Relevance retriever (§2.2.2) | only a request-time `cf` **rerank** of a popularity candidate set | build actual top-k **retrieval** over the store/index |
| Bridging retriever (§2.2.1) | **absent** | the diverse-approval estimator (§3.1) end to end |
| Network/community distance (§2.2.1) | **absent** (follower graph not ingested; `parse_event` drops follows) | follower-graph crawl + community detection |
| Candidate union + topic-diversify (§2.3) | serving builds candidates from popularity only | union, dedup, topic model, diversification |
| Topic model (§2.3, Fig.1) | **absent** | choose + build; resolve topic-retrieval auth |
| Ranker (§2.4) | `baseline/rank.py` (HaR-bin → popularity → AR), **unwired** (no producer) | multi-objective scorer + a wired feed producer |
| HaR / AR (§2.4) | deterministic heuristic stubs in `scorers.py` | fine-tuned, multilingual models |
| Eval: audience diversity / bridging / toxicity / network (§5) | `evals` has **author** entropy + engagement stats only | the entire §5 metric set |
| Exposure model | **absent** | "who could have seen it" denominator for the null (§3.1) |
| Serving store, feed-gen endpoint, MLflow, catalog (infra) | present and working | reuse as-is |

## 5. Measurement hazards specific to the offline setting (§5)

- **Offline evaluation is observational.** On historical/replayed data you
  measure realized interactions of feeds that *were* shown, not of the feed
  BridgEU *would* show. Causal claims about "increasing civil cross-community
  interaction" need online experiments (the plan's §5 online phase / infra R4);
  offline results are associational and should be reported as such.
- **Audience-diversity metrics inherit §3.1's circularity and null-model
  requirements.** The same discipline (sample-split, corpus null, degree
  weighting) applies whether the dispersion is a *retrieval score* or an
  *evaluation metric* — and the eval must not reuse the retriever's own fitted
  vectors to grade the retriever.
- **Multilinguality.** Eurosky is European and multilingual; toxicity/
  deliberation models differ sharply in language coverage (Perspective is
  broadly multilingual; `toxic-BERT` and many deliberation classifiers are
  English-centric). Model selection in §5 must condition on the language mix of
  the corpus, and the corpus's `langs` field (present in `fetch.py`) should be
  carried through so metrics can be reported per language.
- **Community labels for network metrics** (assortativity, modularity,
  cross-community edge share) come from the follower graph (§2.2.1 network
  distance), tying §5's network-polarization metrics to the same follower-crawl
  dependency as the bridging retriever — build the crawl once, use it in both.

## 6. Recommendations

1. **Pin the diverse-approval estimator's specification first** (§3.1: null
   model, sample-splitting, degree weighting) on a synthetic planted-community
   graph *before* touching real data, so the estimator is validated where
   ground truth exists. This is the critical path.
2. **Reconcile Fig.1's rank labels with §2.2.1** (§3.2) — a one-line author
   clarification that unblocks both retrievers.
3. **Resolve the topic-retrieval channel early** (§3.5) — request a greenearth
   Skylight key and/or stand up an authenticated AppView session; two components
   depend on it.
4. **Treat behavioral and network bridging as two measured pathways** (§3.3) and
   report overlap(relevance, bridging) (§3) as a headline diagnostic, per the
   plan's own falsifiable claim.
5. **Keep the estimators swappable** behind the four stable objectives (§4);
   the repo's feature/artifact/algo seams already support this — new estimators
   are new versioned artifacts, not rewrites.
6. **Carry `langs`, exposure, and realized-negative signals through ingest**
   now (small changes) so the null model (§3.1), multilingual eval (§5), and the
   negative-interaction question (§3.7) are not blocked later by missing raw
   fields.

The architecture's stable core — retrieve for relevance, retrieve for
demonstrated cross-audience approval, rank the union for relevance + bridging +
quality − harm — is coherent and implementable. The risk is concentrated in the
*estimators*, above all the diverse-approval score, whose specification must be
made deliberately before any result it produces can be read.
