# 2026-09-04 — Revised pipeline diagram as an editable .pptx

## Approved plan
Turn the revised "Full pipeline" diagram in `../architecture.md` (the variant
where `Follower graph` descends from a dedicated `NETWORK SNAPSHOT` source
rather than from `NEW POSTS INGESTED`) into a single editable PowerPoint slide.
Constraints from the request: every element must be a native, editable object
(no flattened image); visually cleaner for a stakeholder audience; keep the
structure simple.

## Files
| File | Description |
| --- | --- |
| `build_pptx.py` | Generator. Builds one 16:9 slide (13.333 x 7.5 in) with native rounded-rectangle nodes and straight/elbow connectors via `python-pptx`. Run with `uv run --with python-pptx python build_pptx.py`. |
| `bridging_ranker_pipeline.pptx` | Output deck, one slide, 42 native shapes. Every node and arrow is selectable and editable in PowerPoint / Keynote / Google Slides. |
| `preview.png` | Static `matplotlib` mock rendered from the same coordinate math, used only for layout QA (LibreOffice is not installed here, so the .pptx itself could not be rasterised). |

## Layout
Seven stacked rows, top to bottom:
1. Sources: `NETWORK SNAPSHOT` (left) and `NEW POSTS INGESTED` (spanning the interaction + content columns).
2. Raw data: `Follower graph`; `Interaction snapshots (likes / reposts)`; `Content (texts / URLs)`.
3. Representations / models: `Structural embedding zᶜ_u`; `Latent representation`; `Topic model`; `Harm (HaR) model`.
4. Retrieval: `Bridging Retriever - top-K`; `Relevance Retriever - top-K`.
5. `Candidate set — union / dedup / topic diversification`.
6. `Ranker — recency / low harm / bridging`.
7. `Final Top-K Feed` (accent colour).

Edge labels carry the signals that were separate nodes in the ASCII source:
`structural`, `engagement`, `relevance`, `top-K_bridge`, `top-K_rel`,
`topic diversify` (Topic model into the candidate set), `HaR score`
(Harm model into the ranker).

## Design choices considered
- **Native shapes vs. an exported image.** Chosen: native `python-pptx`
  rounded rectangles + connectors, because the request is an *editable* deck.
  An image export (matplotlib/graphviz -> PNG) would look cleaner faster but
  is not editable, so it is used only for QA.
- **`python-pptx` vs. the skill's `html2pptx` workflow.** Chosen: `python-pptx`.
  `html2pptx` targets text-heavy slides; precise flow-diagram placement with
  many connectors is more directly controlled with absolute coordinates.
- **Node reduction.** The ASCII diagram's standalone `Structural bridging`,
  `Topical/engagement Bridging`, and `Relevance` signal boxes are collapsed
  into edge labels, and `Candidate union + dedup` + `Diversify topic` are
  merged into one `Candidate set` node. This drops the diagram from ~9 rows
  to 7 without losing any data path — the simplification the request asked for.
- **Palette.** One calm, infrastructural scheme: navy sources, two-step teal
  for data then representations, slate for the optional content models, amber
  for retrieval, deep teal for the merge/rank spine, and a single coral accent
  used exactly once on `Final Top-K Feed` so the eye lands on the output.
- **Dependency handling.** `python-pptx` is not in the project `uv` env and was
  not added to it; the script is run with `uv run --with python-pptx` so the
  project lockfile is untouched.

## Caption (also embedded as slide speaker notes via `add_notes.py`)

**Bridging Ranker — For-You Pipeline.** The pipeline builds a user's feed in
two phases: multi-source retrieval, which gathers a broad pool of candidate
posts, and ranking, which orders that pool into the final feed.

**a. Multi-source retrieval.** Signals are drawn from two ingestion streams
that run on independent schedules. A dedicated network snapshot provides the
follower graph through a periodic crawl, while the new-posts stream provides
interaction snapshots (likes, reposts) and post content (texts, URLs); keeping
them separate lets the graph refresh on its own cadence rather than once per
post. Each stream is converted into features: the follower graph into a
structural embedding `zᶜ_u` for every user, the interaction snapshots into a
latent collaborative-filtering representation of user preference, and the
content into a topic model and a harm (HaR) model. Two retrievers then query
this pool in parallel. The bridging retriever looks for posts that win approval
across otherwise-separate audiences, scoring them from the structural embedding
together with an engagement signal derived from the latent representation. The
relevance retriever looks for posts that match the user's own tastes, scoring
them from the latent representation alone. Their two top-K lists are merged,
de-duplicated, and spread across topics using the topic model so that no single
subject dominates the candidate set.

**b. Ranking.** The diversified candidate set is ordered by a single
multi-objective score that rewards recent posts, penalises harmful ones using
the HaR score routed directly from the harm model, and rewards bridging
strength carried over from retrieval. The top of this ranking is returned as
the final Top-K feed shown to the user.

## Notable findings
- The `NETWORK SNAPSHOT` split is clean: the follower-graph chain
  (`NETWORK SNAPSHOT -> Follower graph -> Structural embedding -> Bridging
  Retriever`) no longer shares a parent with the post-ingest chains, so the
  two ingestion cadences (periodic network crawl vs. streaming posts) are now
  visually independent.
- Bridging Retriever is the only node with two upstream representation inputs
  (`structural` + `engagement`); everything else is a single path, which is
  what keeps the 7-row version readable.
- LibreOffice is absent on this machine, so end-to-end rasterisation of the
  .pptx was not possible; layout was verified against `preview.png`, which is
  generated from the identical coordinate table in `build_pptx.py`.
