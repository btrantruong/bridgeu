"""Time-benchmark the *unauthenticated* Jetstream firehose for topic-scoped
network construction — the no-auth alternative to the (blocked) searchPosts.

Reuses the repo's own consumer (`data_engineering.ingest.jetstream`) so the
numbers reflect the production ingest path, not a toy client. Collects a live
window bounded by BOTH wall-time and a max-event cap, then reports:

* throughput (events/sec) and the post/like/repost mix;
* reply edges available in-window (post events whose `subject_uri` is set =
  a reply to a parent) — the raw material of a reply network;
* keyword hit-rate: how many in-window posts match a topic term, i.e. how
  long you must listen to accumulate a topic slice of a given size.

Run:  uv run python bench_jetstream.py --seconds 25 --keyword climate
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter

from data_engineering.ingest.jetstream import DEFAULT_COLLECTIONS, stream_events


async def collect(seconds: float, max_events: int, keyword: str | None) -> dict:
    counts: Counter = Counter()
    reply_edges = 0
    kw_posts = 0
    n_posts = 0
    t0 = time.perf_counter()
    n = 0
    async for row in stream_events(DEFAULT_COLLECTIONS):
        counts[row["collection"]] += 1
        if row["collection"] == "app.bsky.feed.post":
            n_posts += 1
            if row.get("subject_uri"):
                reply_edges += 1  # this post is a reply -> (author -> parent_author) edge
            if keyword and row.get("text") and keyword.lower() in row["text"].lower():
                kw_posts += 1
        n += 1
        if n >= max_events or (time.perf_counter() - t0) >= seconds:
            break
    elapsed = time.perf_counter() - t0
    return {
        "elapsed_s": round(elapsed, 2),
        "events": n,
        "events_per_s": round(n / elapsed, 1) if elapsed else None,
        "by_collection": dict(counts),
        "posts": n_posts,
        "reply_edges_in_window": reply_edges,
        "keyword": keyword,
        "keyword_post_hits": kw_posts,
        "keyword_hit_rate": round(kw_posts / n_posts, 5) if n_posts else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=25.0)
    ap.add_argument("--max-events", type=int, default=60000)
    ap.add_argument("--keyword", default="climate")
    ap.add_argument("--out", default="../outputs/jetstream_bench.json")
    args = ap.parse_args()

    print(f"listening to Jetstream for up to {args.seconds}s / {args.max_events} events ...")
    res = asyncio.run(collect(args.seconds, args.max_events, args.keyword))
    import json

    res["params"] = vars(args)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps(res, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
