# Data sources for BridgEU offline development — investigation and time benchmark

**Date:** 2026-09-15
**Author:** DS (offline development)
**Scope:** Assess the two data sources named in the task brief — the Green Earth
API (`https://api.greenearth.social/docs`) and the Bluesky PDS
(`https://github.com/bluesky-social/pds`) — against the concrete need to
(a) explore the follower graph and (b) construct mention/reply networks for a
given topic, with a simple, reproducible time benchmark. Situate both against
the ingest paths the repo already ships.

---

## 1. The task, as executed

> Investigate `api.greenearth.social` and `bluesky-social/pds`; see what is
> viable; provide a simple time benchmark; find an easy way to construct
> mention/reply networks for a given topic and to explore the follower graph.

Executed by (1) reading each API surface, (2) probing reachability and auth,
and (3) benchmarking the *unauthenticated public Bluesky AppView* and the
*Jetstream firehose* — the two sources that actually answer the graph/topic
questions — with the repo's own ingest code where possible.

## 2. Files produced

| file | what it is |
|---|---|
| `scripts/bench_appview.py` | parameterized, stdlib-only benchmark of public-AppView methods: seed resolution, follower-graph pagination, seed-post sourcing, reply/mention-network construction from `getPostThread`, and like-edge harvest from `getLikes` |
| `scripts/bench_jetstream.py` | live-firehose benchmark using the repo's `data_engineering.ingest.jetstream.stream_events`: throughput, collection mix, in-window reply edges, and keyword hit-rate |
| `outputs/appview_bench.json` | raw results of the AppView run (seeds, timings, edge counts) |
| `outputs/jetstream_bench.json` | raw results of the 25 s firehose window |

Both scripts are re-runnable (`uv run python <script>`); all inputs — seed
handles, page budgets, seed feed, keyword, pacing — are CLI parameters.

## 3. The two named sources — verdicts

### 3.1 Green Earth API (`api.greenearth.social`) — a feed *service*, not a data lake

The OpenAPI spec describes a **recommendation service** (FastAPI, v0.1.0), not a
data-access API. Endpoint families: `/candidates/generate` (registered
candidate generators), `/rank/predict` (ranking models), `/diversify`,
`/skylight/search` (Elasticsearch-syntax post search), `/api/feeds/*` (feed
transparency/preview), and the ATProto feed-generator XRPC trio
(`describeFeedGenerator`, `getFeedSkeleton`, `sendInteractions`).

- **Auth & status (tested 2026-09-15 with a DS-held `X-API-Key`).** Full
  end-to-end smoke test in `scripts/test_greenearth.py`, capabilities recorded
  in `outputs/greenearth_capabilities.md`. All key-gated endpoints return 200 in
  0.2–0.7 s: `/skylight/search`, `/candidates/generators`, `/rank/models`,
  `/candidates/generate`, `/rank/predict`, `/diversify`. (`/api/feeds` needs a
  session bearer token, not the API key → 401.) It is a **working reference
  recommender service**, not merely a search box.
- **What it gives us (verified):** (1) **topic search** (`/skylight/search`,
  Elasticsearch syntax) returning posts with a `minilm_l12_embedding`; (2)
  ready-made **384-d L2-normalized MiniLM content embeddings** (base64 float32,
  ‖v‖=1) usable directly for cosine/dot; (3) a per-post **`politics_score` ∈
  [0,1]** partisanship signal and real **`like_count`**, both populated by
  `/candidates/generate` (user-conditioned) — *note:* search returns the
  embedding but leaves `politics_score`/`like_count` null; (4) a multi-source
  **generate → rank → diversify** pipeline whose generators include `two_tower`
  (the §4 CF-upgrade) and `network_likes`, and whose rankers include
  `perspective` (toxicity) and `heavy_ranker` — a useful baseline to benchmark
  our own against.
- **What it still does not provide (confirmed):** no firehose/stream, **no
  follower/following graph, no reply/mention structure, no exposure logs, and no
  raw `(user, post)` like edges**. `politics_score`/embeddings are *service
  outputs* (per user-conditioned candidate or per topic search), not a bulk raw
  feature keyed by arbitrary URI. Interactions are write-only via
  `sendInteractions`.

**Verdict:** with the key, greenearth is a genuinely useful **accelerator** for
the *content* side — a drop-in content embedding, a partisanship score, topic
search, and a reference pipeline (incl. a two-tower generator and a
Perspective ranker) to compare against. It still **cannot** supply the social
graph, reply structure, exposure, or raw engagement edges the bridging objective
and the offline evaluation need; those remain a Jetstream/AppView + platform-
data-request concern. Treat it as the relevance/topic/partisanship accelerator,
not a foundation.

### 3.2 Bluesky PDS (`bluesky-social/pds`) — single-user host, wrong tool

The repository ships container images and docs for **self-hosting one account's
Personal Data Server**. A PDS stores *that account's* repo (its posts, likes,
follows) and exposes XRPC over its own domain; it is a write/authority node in
the federation, not a read view over the network. It cannot answer "who follows
X", "the replies to post Y", or any cross-account query — those are AppView/
relay concerns.

**Verdict:** **not viable** for follower-graph or mention/reply-network
research. Standing one up would give us our own account's data and nothing
else. The network-wide reads it *cannot* serve are exactly what we need, and
they come from the relay (firehose) and the AppView — below.

### 3.3 What actually answers the brief (and is already half-wired in the repo)

- **Jetstream** (`wss://jetstream2.us-east.bsky.network`) — the JSON firehose,
  server-side filterable by collection, replayable by cursor. The repo's
  `jetstream-slice` / `jetstream-tail` already consume it. Identity-preserving
  `(user, post)` like/repost events and reply edges (`post.subject_uri`) fall
  out of it directly.
- **Public Bluesky AppView** (`public.api.bsky.app/xrpc`) — unauthenticated
  ATProto read methods. `fetch-corpus` already uses `getFeed`. The graph
  (`getFollowers`, `getFollows`), thread (`getPostThread`), and engagement
  (`getLikes`, `getRepostedBy`) methods are open and are what build the
  follower graph and reply networks.
- **One caveat that shapes everything below:** `app.bsky.feed.searchPosts` is
  **blocked unauthenticated** (HTTP 403, Cloudflare page) on the public host —
  confirmed, and already noted in the repo's `fetch.py`. Topic-scoped *search*
  therefore requires either an authenticated AppView session (app-password →
  JWT), the greenearth Skylight key, or a topical feed generator. Topic scoping
  by **keyword filter over the firehose** is always available but is expensive
  for narrow topics (§5.3).

## 4. Benchmark — method

All timings are wall-clock per HTTP GET measured with `time.perf_counter`
(`urllib`, 30 s socket timeout), sequential, paced 50 ms apart to stay well
under public rate limits; the firehose figure is a single 25 s websocket
window. Hardware/network: the development host on 2026-09-15; absolute numbers
will move with location and load, so the **ratios and hit-rates** are the
portable findings, not the milliseconds. Seed accounts are three ATProto-core
handles (`bsky.app`, `jay.bsky.team`, `pfrazee.com`); seed posts are 40 items
sourced from the public "What's Hot" feed generator (the only unauthenticated
post source, since `searchPosts` is blocked).

## 5. Benchmark — results

### 5.1 Public AppView throughput

**Table 1. Unauthenticated public-AppView latency and edge throughput.** Each
row is one XRPC method. *Latency* is median seconds per GET over *N* sequential
calls. *Edges/s* = total records returned ÷ total request wall-time (a
single-thread, paced ceiling; parallelism and dropped pacing raise it). Follower
pages are 100 accounts; like pages are 100 likers; the reply row aggregates
`getPostThread` calls over the 37 seed posts that had ≥1 reply.

| method | calls (N) | median s/call | records | edges/s | what one record is |
|---|---|---|---|---|---|
| `actor.getProfile` | 3 | 0.77 | 3 | — | a seed's DID + graph sizes |
| `graph.getFollows` | 1 | 0.80 | 15 | 18.8 | a followed account |
| `graph.getFollowers` | 3 | 1.00 | 288 | 98.7 | a follower account |
| `feed.getFeed` (What's Hot) | 1 | 1.27 | 40 | — | a candidate post (seed source) |
| `feed.getPostThread` | 37 | 0.75 | 1271 reply edges | 44.3 | a reply → parent edge |
| `feed.getLikes` | 10 | 0.88 | 1000 | 111.3 | a `(liker, post)` like edge |

Seed graph sizes (context for crawl-cost projection, from `getProfile`):
`bsky.app` 34.9 M followers / follows 15; `jay.bsky.team` 594 k / 3 982;
`pfrazee.com` 309 k / 704.

### 5.2 Reply / mention network construction — it works, and how

From 40 "What's Hot" seed posts, 37 had replies. `getPostThread` (depth 6) over
those 37 yielded a **reply network of 1 017 nodes and 1 271 directed
`(author → parent-author)` edges in 28.7 s** — i.e. a usable interaction graph
from one seed feed in under half a minute. Mechanics (in `bench_appview.py`):
walk the nested thread; each reply post contributes an edge from its author to
the DID in `record.reply.parent.uri`. **Mentions** are read from richtext
facets (`app.bsky.richtext.facet#mention` → `did`); in this sample the hot posts
carried **zero** mention facets, so mention edges were 0 — mentions are real but
sparse in trending content and are better harvested from a topic slice than from
"What's Hot".

### 5.3 Jetstream firehose — throughput, mix, and the topic-cost finding

**Table 2. One 25 s unauthenticated Jetstream window** (repo consumer, default
collections). *Events/s* is delivered client-side rate (network + per-message
JSON parse bound; the relay's native rate is higher). *Reply edges in-window* =
post events whose `subject_uri` is set. *Keyword hit-rate* = posts containing
"climate" ÷ all posts in-window.

| quantity | value |
|---|---|
| window | 25.17 s, 7 016 events |
| delivered rate | 278.8 events/s |
| collection mix | 5 235 likes : 968 posts : 813 reposts |
| replies among posts | 387 / 968 ≈ 40 % (direct reply edges) |
| keyword "climate" hits | 2 / 968 posts = 0.21 % |

**The decision-relevant finding.** Any single topic is a tiny fraction of global
traffic: at 0.21 % hit-rate and ≈ 38 posts/s, accumulating a 500-post "climate"
slice from the raw firehose would take ≈ 500 ÷ (38 × 0.0021) ≈ **1.7 hours**,
discarding 99.8 % of delivered bandwidth — and longer for rarer terms. So:

- The firehose is the right source for **engagement/CF ingestion** (likes
  dominate the mix 5:1 — dense `(user, post)` signal) and for **broad reply-edge
  harvest** (40 % of posts are replies, edges are free in-stream).
- The firehose is the **wrong** source for **narrow topic retrieval**. Topic
  scoping wants an *index*: authenticated `searchPosts`, greenearth Skylight, or
  a topical feed generator — not keyword-filtering the stream.

## 6. Recommended access patterns (the "easy way" the brief asks for)

1. **Explore the follower graph** — BFS from seed DIDs via `graph.getFollows`
   / `graph.getFollowers`, 100/page, ≈ 1 s/page single-thread (~100 accounts/s).
   Budget: a 10 k-node ego-crawl ≈ a few minutes paced; a hub like `bsky.app`
   (35 M followers) is not crawlable edge-by-edge and should be sampled or
   excluded. This is the source for the **network-distance / community** signal
   in the bridging retriever.
2. **Reply/mention network for a topic** — two options, pick by freshness need:
   - *Indexed (needs auth/key):* `searchPosts` or Skylight for the topic's
     posts → `getPostThread` per root → reply edges; facets → mention edges.
   - *Streamed (no auth):* a Jetstream window keyword/facet-filtered to the
     topic → reply edges directly from `subject_uri`, mention edges from record
     facets. Cheap to run, but narrow topics need long windows (§5.3).
3. **Engagement (CF) edges** — either `getLikes` per corpus post from the
   AppView (≈ 111 edges/s; deterministic, backfillable — this is exactly the
   `getLikes`-per-post backfill flagged as `I1` open in the infra track) or a
   Jetstream slice's like events (live, denser, but only a rolling window).

## 7. Notable findings

- **PDS is out**; **greenearth is a keyed accelerator, not a foundation**; the
  load-bearing sources are Jetstream + the public AppView, both already partly
  wired in `data_engineering.ingest`.
- **`searchPosts` being unauth-blocked is a real constraint**, not an
  oversight — it forces an explicit choice for topic scoping (auth session vs
  Skylight key vs topical feed vs firehose filter). This should be settled early
  because the bridging *retriever* and the topic-diversification step both
  depend on a topic handle.
- **Likes are abundant, mentions are sparse, replies are ~40 % of posts.** The
  CF signal is cheap; the reply-network signal is cheap; a mention network is
  viable but should be built from a topic slice, not trending content.
- **All numbers are single-thread and paced.** They set honest floors; modest
  concurrency (respecting rate limits) and caching move throughput up an order
  of magnitude without changing any of the qualitative conclusions.
- **Reproducibility:** re-running either script regenerates the JSON in
  `outputs/`; absolute latencies will differ by network, the ratios should not.
