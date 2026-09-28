# 2026-09-28 — MySky API study, integration design, and revised scope of work

## 1. The approved plan

Source: `concept/todo_2026-09-28.md`, executed on the instruction "execute to do".

> **Big goal:** a For-You feed pipeline that works in production; a scope of tasks for MySky engineers.
>
> **Task 1.** Read `./api`, distill learnings as context for the next session. The DeepWiki for the
> components (https://deepwiki.com/greenearth-social/api) may be used — ask permission before going
> on the web.
>
> **Task 2.** Extract the components of `./api` that can be reused for the BridgEU pipeline; write a
> design brief to interface with the engineers. Write an implementation note describing how Eurosky,
> `./api` and the DS pipeline work together.
>
> **Task 3.** Revise the scope of tasks for MySky engineers (`concept/wps.md`): tasks that help DS and
> Eurosky engineers bring the current pipeline into production, and that build infrastructure useful
> for Eurosky later, including A/B tests on Eurosky.
>
> Constraints: do not read `./eurosky` again without permission (its learnings are in
> `concept/design/eurosky-repo-learnings/`); do not access the S3 bucket or
> `concept/analysis/credentials.txt`.

Execution notes: web access was granted (DeepWiki, read-only). `./eurosky` and the credentials file
were not opened. No code in `./api` was changed or run; this was a reading and design task, so there
are no scripts or intermediate data files.

## 2. Files produced

| file | task | audience | content |
|---|---|---|---|
| `api-learnings.md` | 1 | next agent session / DS | system map around `./api`; endpoints; the `getFeedSkeleton` path step by step; the generator and ranker plugin interfaces; ES indices and Firestore collections; operational patterns; constraints for BridgEU; open questions. Each claim tagged `[code]`, `[wiki]` or `[inferred]` |
| `design-brief.md` | 2 | MySky + Eurosky engineers, DS | what BridgEU needs in engineering terms; reuse map of `./api`; the offline→online seam and the artifact contract (A1–A5); new generators/ranker; framework changes E1–E7; decisions D1–D4 with options and proposals; questions for MySky |
| `implementation-note.md` | 2 | engineers + DS | roles of the three codebases; architecture diagram; where each DS block is implemented; implementation choices with alternatives considered; refresh and request flows; version thread; defaults and failure behaviour; rollout order (shadow → internal → public → experiments) |
| `wps-revised.md` | 3 | MySky engineers (after DS review) | revised scope: change table vs `concept/wps.md`; phases P0–P4 with exit criteria; work packages WP0–WP9 with tasks, owners, acceptance criteria; dependencies on DS/Eurosky; parameter list |
| `2026-09-28-mysky-integration.md` | — | project record | this file |

No plots were produced.

## 3. Notable findings

Findings are about the specification and the fit between systems, not about any estimate.

1. **BridgEU fits MySky as configuration plus three plugins.** MySky's feed is a declarative
   `FeedConfig` over named generators and rankers behind two small abstract classes. The request
   path, auth, cursoring, exclusions, interaction logging, monitoring and deploy/rollback already
   exist, so the original WPS item "implement main getFeedSkeleton endpoint" is unnecessary.
2. **MySky slates are an order of magnitude smaller than the DS design assumed.** A cursor session
   generates `min(limit × 5, 100)` candidates (200 with a flag), split across generators by weight.
   The DS design (`2026-09-03-foryou-pipeline-design.md`) assumed |S| ≈ 500–1 000. Retrieval quotas,
   the within-slate percentile normalization of DS §5.1, and the overlap(relevance, bridging)
   measurement are all defined relative to |S|, so their meaning changes with this size.
3. **MySky's score combiner embeds three modelling choices the DS ranker does not share:**
   normalization by declared theoretical bounds (not within-slate percentiles); a missing ranker
   score is filled with that ranker's slate median (the DS design declares "no evidence" = null
   value); and there is no hard gate, only a floor on the combined score. A user-controlled politics
   multiplier then scales the result by a politics-topic probability, which can remove bridging
   posts by construction when set to 0.
4. **Seen and discarded exclusions are per user across all MySky feeds.** A post seen or discarded
   in one feed is excluded from every other feed of that user — an interference channel for any
   experiment where users read more than one feed.
5. **MySky holds identity-preserving like edges and an exposure log.** The ES `likes` index stores
   (liker, post, time); `sendInteractions` delivers `interactionSeen` per user and post for
   MySky-served feeds. These are the inputs DS's data request rated critical (likes) and highest
   marginal value (exposure). Served-slate snapshots, however, expire after 24 h, and experiment
   flag values are recorded neither on snapshots nor on `feedLoaded`.
6. **Per-item source attribution already exists for served items.** Snapshots list every generator
   that returned each served post (pre-dedup), and the recorder holds full per-generator outputs on
   every request; only candidates cut before serving are not persisted outside debug records.
7. **No global follow graph exists in MySky.** It caches outbound follow lists (≤ 1 000) for its own
   users only, patched from Jetstream by ingex. Unfollows only invalidate a cached list, consistent
   with Jetstream delete events carrying the record key rather than the followed DID. A rewindable
   graph therefore needs a follow snapshot that includes record keys, plus an rkey → subject map.
8. **Two existing MySky patterns match BridgEU's needs directly:** the shared popularity candidate
   pool with a leased background refresh (the shape of a viewer-independent or per-community
   bridging pool), and the average-user-embedding artifact promotion (immutable artifact → schema
   validation → pointer → pinned at deploy), which is the shape of a hand-refreshed community table
   crossing org boundaries.
9. **The original WPS asks for more on the request path than is needed.** Of the four community
   mappings, only user → communities and community → outgroups are request-path lookups; ingroup
   user lists are batch inputs, and banners/names serve only the transparency view.
10. **The DeepWiki is stale relative to the code** (indexed 2026-03-10; lists a `post_similarity`
    generator that no longer exists; no page for `engagement-prediction`). Code was treated as
    authoritative.

## 4. Decisions awaiting sign-off

D1 (where follow graph and community detection live), D2 (integration level with MySky), D3
(granularity of bridging retrieval), D4 (data residency) — options and proposals in
`design-brief.md` §6. DS specification items raised: score transform for the bridging ranker,
declared defaults for abstained posts, representation used for behavioral distance (O3) versus
relevance (R1) (`implementation-note.md` §4.5–4.7).
