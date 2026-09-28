# 2026-09-15 — data sources, conceptual evaluation, and implementation plans

Response to the task brief: investigate the candidate data sources with a time
benchmark, evaluate the `overview.md` conceptual plan, and draft modular,
parallelizable implementation plans (including experiment-path-dependent
sub-plans and an evaluation plan for §5).

## Contents

| file | what it is |
|---|---|
| `2026-09-15-data-sources.md` | data-source investigation + reproducible time benchmark; verdicts on greenearth API and Bluesky PDS; recommended access patterns for follower graph and reply/mention networks |
| `2026-09-15-conceptual-evaluation.md` | specification-correctness review of `overview.md` + Fig.1; the design decisions that gate implementation; repo↔plan gap analysis |
| `2026-09-15-implementation-plans.md` | modular workstreams (WS-D/R/S/A/E), dependency graph, the §4 experiment path-dependency gates, and the §5 evaluation plan |
| `2026-09-15-data-request.md` | third-party data request (brief + detailed spec) for the platform: what offline development needs, with field-level schemas, priorities, and privacy notes |
| `2026-09-15-data-schema-spec.md` | build-ready table schemas (Parquet/SQL datatypes, keys, ER diagram) split into Phase 1 / Phase 2 for a platform engineer to generate the dataset |
| `scripts/bench_appview.py` | benchmark: public-AppView follower-graph, reply/mention networks, like edges |
| `scripts/bench_jetstream.py` | benchmark: Jetstream firehose throughput + topic keyword hit-rate |
| `scripts/test_greenearth.py` | keyed smoke test of the Green Earth API (reads `GEA_KEY` from env; no key stored) |
| `outputs/appview_bench.json` | raw AppView benchmark results |
| `outputs/jetstream_bench.json` | raw firehose benchmark results |
| `outputs/greenearth_capabilities.md` | tested Green Earth endpoints, schemas, and what it does/doesn't provide |

## Headline findings

- **Data:** Bluesky PDS is single-user (out); greenearth (keyed, **tested
  2026-09-15**) is a working recommender service — topic search + ready-made
  384-d MiniLM embeddings + a `politics_score` + a reference generate/rank/
  diversify pipeline — but supplies no graph, replies, exposure, or raw
  engagement edges. The load-bearing raw sources remain **Jetstream** + the
  **public AppView**, both already partly wired in the repo. Follower graph ≈ 100
  accts/s/page; a 1 017-node / 1 271-edge reply network built from one seed feed
  in 29 s; unauthenticated `searchPosts` is blocked, so **narrow topic retrieval
  needs an index** (Skylight/auth/topical feed), not firehose keyword filtering
  (a 500-post topic slice ≈ 1.7 h).
- **Plan:** architecture core is coherent; risk concentrates in the
  **diverse-approval estimator**, whose null model, sample-split, and
  degree-weighting must be specified before any estimate is interpretable.
- **Build:** parallelizable into a data foundation, three representation tracks,
  five scorers, assembly, and evaluation, decoupled by versioned artifacts; the
  long pole is the S2 estimator spec, validated on a synthetic planted-community
  graph first.
