# Data request — offline development of the BridgEU bridging recommender (DS side)

**Date:** 2026-09-15
**From:** Data-science team (offline development & evaluation)
**To:** Eurosky platform engineering
**Purpose:** Specify the data the DS team needs to build and evaluate the
BridgEU For-You feed offline. Two versions follow: **Part A** is a one-page
summary suitable for an email; **Part B** is the detailed field-level
specification. Schemas are deliberately aligned to the row shapes our pipeline
already ingests, so a delivery drops in without transformation.

---

# Part A — Brief version

We are developing a two-stage bridging recommender (retrieve for personal
relevance + retrieve for cross-audience approval, then rank) and need
**pinned, immutable snapshots** of the network to develop and evaluate it
offline. Some of this we can pull ourselves from public Bluesky APIs, but it is
slow and partial; the items marked *(platform-only)* are ones we effectively
cannot get without you.

We need, keyed by ATProto DIDs and `at://` URIs throughout, delivered as JSONL
or Parquet:

1. **Positive-interaction events** — every *like* and *repost* as an
   identity-preserving `(actor, post, timestamp)` triple (not aggregate
   counts). This is the raw material for collaborative filtering and the
   bridging audience. *Critical.*
2. **Posts (content) with engagement counts** — post URI, author, timestamp,
   text, language(s), reply/quote references, external URLs/media flags, and
   as-of-snapshot like/repost/reply/quote counts. *Critical.*
3. **Reply & thread data** — replies with their parent and root URIs and text,
   so we can reconstruct reply/mention networks and train the harmful-response
   model. *Critical.*
4. **Follower-graph snapshot** — directed follow edges `(follower, followed)`
   over the accounts in the snapshot, with a snapshot timestamp. *(Faster and
   more complete from you than crawling.)* *High.*
5. **Exposure / impression logs** — which viewer was shown which post, when,
   in what position. This is the single most valuable thing you can provide
   because it is **not available on any public API**, and without it offline
   evaluation and the bridging "expected diversity at comparable reach" baseline
   are badly biased. Sampled/aggregated is fine. *(platform-only)* *High.*
6. **A Skylight API key** (or an authenticated AppView session) so we can do
   topic-scoped search — unauthenticated `searchPosts` is blocked. *Medium.*
7. **Optional:** negative/moderation signals (un-likes, deletes, blocks, mutes,
   moderation labels) and any content embeddings / partisanship scores you
   already compute. *Optional but valuable.*

**Shape of the ask:** one **historical backfill** (a fixed multi-week window,
pinned and versioned) to develop against, plus a **rolling refresh** (e.g.
daily or weekly) once we move to freshness testing. Identifiers stable and
joinable across all datasets; pseudonymized DIDs are acceptable provided they
are stable and joinable. Please also confirm actual volumes and any
privacy/legal constraints (GDPR) so we size storage and scope access correctly.

If you can only send one thing first, send **(1) like/repost events** for a
fixed 2–4 week window — it unblocks the most.

---

# Part B — Detailed specification

## B.0 Principles

- **Pinned & immutable.** Each delivery is a named, versioned snapshot over an
  explicit time window `[t0, t1)`; re-deliveries are new versions, never
  in-place edits. Reproducibility of an offline result = same snapshot + same
  code.
- **Identity-preserving events, not counts.** A like is the pair *(who, what,
  when)*. Counts are derivable from events; events are never recoverable from
  counts. Deliver the events.
- **Canonical identifiers.** ATProto DIDs (`did:plc:…`) for actors/authors and
  `at://…` URIs for records, everywhere, so datasets join without ambiguity.
- **Formats.** JSONL (one record/line) or Parquet (preferred at scale;
  `parquet-tools`-readable). UTF-8, ISO-8601 timestamps in UTC, plus the
  Jetstream `time_us` (unix microseconds) where available for exact ordering.
- **Stable schema + changelog.** Fixed field names; additive changes announced.
- **Privacy by design** (see B.8).

## B.1 Positive-interaction events *(Critical)*

Likes and reposts, one row per event. Matches our raw-event row exactly, so it
lands directly in the feature pipeline.

| field | type | notes |
|---|---|---|
| `time_us` | int | unix microseconds (event order) |
| `did` | string | actor DID (who liked/reposted) |
| `collection` | string | `app.bsky.feed.like` or `app.bsky.feed.repost` |
| `uri` | string | the like/repost record URI |
| `subject_uri` | string | the liked/reposted **post** URI |
| `subject_author` | string | DID of the post's author |
| `created_at` | string | ISO-8601 (record's own timestamp) |

- **Window:** one pinned backfill of **≥ 2–4 weeks**; then rolling
  daily/weekly increments.
- **Completeness:** all events in-window for the account universe of the
  snapshot (not a feed-filtered subset), so co-engagement structure is intact.
- **Uses:** CF user/post factorization; the positively-engaged audience whose
  dispersion defines bridging; engagement labels for evaluation.

## B.2 Posts / content corpus *(Critical)*

One row per post in-window. Matches our corpus record.

| field | type | notes |
|---|---|---|
| `uri` | string | post `at://` URI |
| `cid` | string | content id |
| `author.did` | string | author DID |
| `author.handle` | string | optional |
| `text` | string | post text |
| `created_at` | string | ISO-8601 |
| `langs` | string[] | declared languages (needed for multilingual eval) |
| `reply_parent_uri` | string\|null | set for replies |
| `reply_root_uri` | string\|null | thread root |
| `facets` | json | richtext facets incl. **mentions** (`#mention` → DID) and links |
| `embed_urls` | string[] | external link URLs |
| `contains_images` / `contains_video` | bool | media flags |
| `like_count` / `repost_count` / `reply_count` / `quote_count` | int | **as-of the snapshot time** (state the reference time) |

- **Window & universe:** same window as B.1; include at least every post that
  received in-window engagement, plus (ideally) all posts authored in-window.
- **Uses:** topic model, HaR text features, candidate content, engagement
  targets. `langs` and `facets` are load-bearing — please don't drop them.

## B.3 Reply & thread structure *(Critical)*

Can be a view over B.2 (replies carry parent/root) **or** delivered as edges.
If as edges:

| field | type | notes |
|---|---|---|
| `reply_uri` | string | the reply post |
| `reply_author` | string | DID |
| `parent_uri` | string | immediate parent post |
| `parent_author` | string | DID |
| `root_uri` | string | thread root |
| `created_at` | string | ISO-8601 |
| `text` | string | reply text (for toxicity/deliberation scoring) |

- **Uses:** reply/mention networks (network-polarization eval); realized-reply
  toxicity as **training labels for the harmful-response (HaR) model** and as
  the deliberative-quality corpus.

## B.4 Follower-graph snapshot *(High)*

Directed follow edges over the snapshot's account universe.

| field | type | notes |
|---|---|---|
| `follower_did` | string | source |
| `followed_did` | string | target |
| `created_at` | string\|null | edge creation time if available |
| `snapshot_at` | string | when the graph was read |

- **Universe:** at minimum, the accounts that authored or engaged in-window
  (so the graph aligns with B.1/B.2); a wider ego-neighborhood (1–2 hops) is
  better for community detection.
- **Why from you:** public crawling runs ≈ 100 accounts/second/token and is
  infeasible for hub accounts (tens of millions of followers); a server-side
  snapshot is far cheaper and more complete.
- **Uses:** community detection / network distance for bridging; assortativity,
  modularity, cross-community edge share for evaluation.

## B.5 Exposure / impression logs *(High — platform-only, highest marginal value)*

Which viewer was shown which post, when, and where. **Not obtainable from any
public API**, so only you can supply it, and it disproportionately improves both
the model and the evaluation.

| field | type | notes |
|---|---|---|
| `viewer_did` | string | who was shown the post (pseudonymized ok) |
| `post_uri` | string | what was shown |
| `shown_at` | string | ISO-8601 |
| `surface` | string | feed/context (e.g. following, discover, a feed rkey) |
| `position` | int\|null | rank in the shown list |
| `dwell_ms` | int\|null | optional engagement-time |

- **Sampling:** a representative sample or aggregated exposure counts per post
  are acceptable if full logs are too sensitive/large — even a per-post
  *impression count* materially helps.
- **Uses:** (1) the **exposure denominator** for the bridging "diversity
  expected at comparable reach" null model — without it, bridging scores are
  confounded by popularity; (2) **unbiased offline evaluation** — realized
  engagement conditioned on exposure, rather than the exposure the current feed
  happened to give; (3) counterfactual/replay analysis of candidate
  distributions.

## B.6 Negative & moderation signals *(Optional)*

| signal | shape | use |
|---|---|---|
| un-likes / un-reposts | `(actor, subject_uri, time)` events | realized-negative signal; currently dropped at public ingest |
| deletes | `(uri, time)` | data hygiene; retraction of engagement |
| blocks / mutes | aggregate counts per (a,b) if possible | negative-interaction modeling (privacy-sensitive → aggregate) |
| moderation labels | `(subject_uri or did, label, source)` | filtering + HaR label enrichment |

## B.7 Topic-scoped search & content features *(Medium — partly satisfied)*

**Status:** a Green Earth `X-API-Key` has been provided and tested
(2026-09-15). Skylight topic search and the generate/rank/diversify endpoints
work; `/skylight/search` returns posts with a 384-d L2-normalized MiniLM
embedding, and `/candidates/generate` returns per-post `politics_score` ∈ [0,1]
and `like_count`. This covers topic search and ready-made content embeddings.
The remaining asks are:

- **A bulk hydrate endpoint** (or dump) that returns `minilm_l12_embedding`
  **and** `politics_score` (and `like_count`) for an **arbitrary list of post
  URIs**. Today embeddings come from search and `politics_score` only from
  user-conditioned `/candidates/generate`, so we cannot cheaply score a fixed
  corpus. This is the highest-value addition on the content side.
- **`politics_score` definition & provenance** — label source, model, range
  semantics, language coverage — so we can use it correctly (and know whether it
  can stand in for, or must be validated against, our own viewpoint model).
- **Rate limits** on the key, and Skylight **corpus coverage/freshness** (how
  far back, refresh cadence, any filtering).
- Still useful if available: an **authenticated AppView session/app-password**
  for `searchPosts` as a fallback topic channel independent of Skylight.

Note this endpoint set does **not** replace the raw relational data in B.1–B.6
(engagement edges, graph, replies, exposure): those are service *outputs*, not
the underlying events.

## B.8 Cross-cutting delivery & governance

- **Cadence:** (1) one **pinned historical backfill** to develop against;
  (2) a **rolling refresh** (daily or weekly) once we test freshness. State the
  retention window you can offer.
- **Delivery:** S3-compatible object storage (Parquet at scale), or a shared
  bucket; a small **starter sample** (e.g. one day, one topic) first to validate
  schemas before the full backfill.
- **Volume:** please confirm actual in-window row counts (likes, posts,
  reposts, edges) so we can size storage; our public-API benchmark
  (`2026-09-15-data-sources.md`) only bounds *our* pull rate, not your true
  volumes.
- **Join keys:** DIDs and `at://` URIs consistent across **all** datasets;
  if pseudonymized, the mapping must be **stable over time and shared across
  datasets** so events, content, graph, and exposure all join.
- **Privacy / legal (GDPR):** public content and public engagement, but
  user-level interaction and exposure data are personal data. We commit to data
  minimization, purpose limitation (research/offline development only), access
  control, and an agreed retention/deletion policy. Pseudonymized stable DIDs
  are preferred; please flag any fields you cannot share so we can design around
  them.

## B.9 Priority summary

| priority | datasets | unblocks |
|---|---|---|
| **Must-have** | B.1 like/repost events, B.2 posts, B.3 replies | CF, retrieval, bridging audience, HaR labels, most eval |
| **High** | B.4 follower graph, B.5 exposure logs | network bridging + polarization eval; unbiased eval + null model |
| **Medium** | B.7 topic access / embeddings | topic model, topic diversification, topic-scoped bridging |
| **Optional** | B.6 negative/moderation | realized-negative ranking signal, filtering |

**Minimal first delivery to unblock development:** B.1 (like/repost events) for
a fixed 2–4 week window, with B.2 (posts) for the same window — schemas as
above, one small day-sized sample first.
