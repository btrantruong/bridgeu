"""Time-benchmark the *unauthenticated* public Bluesky AppView for the two
data-access patterns BridgEU needs: (1) exploring the follower graph and
(2) constructing reply / mention networks for a set of seed posts.

Why this host and these methods: `public.api.bsky.app` serves ATProto XRPC
read methods with no auth. `app.bsky.feed.searchPosts` is blocked there
(HTTP 403 / Cloudflare) and greenearth `/skylight/search` needs an API key
(HTTP 401), so topic-scoped retrieval is treated as a separate, documented
concern; here we benchmark the graph/thread/feed methods that *are* open and
that the retrieval + evaluation stages depend on.

Everything is stdlib (urllib) so it runs in any interpreter; run it under the
project venv for parity:  `uv run python bench_appview.py`.

Parameters (all optional, see --help): seed handles, page budgets per method,
the public feed used to source seed posts, keyword filter for a crude topic
slice, request pacing, and the output JSON path.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from statistics import median

APPVIEW = "https://public.api.bsky.app/xrpc"
# Bluesky's "What's Hot" — public feed generator, no auth (same one fetch.py uses).
WHATS_HOT = "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot"
# ATProto-core accounts: present, active, non-trivial graphs. Seeds only.
SEED_HANDLES = ["bsky.app", "jay.bsky.team", "pfrazee.com"]


def xrpc(method: str, params: dict, pace: float = 0.0) -> tuple[dict | None, float, int]:
    """One timed GET. Returns (json_or_None, elapsed_seconds, http_status)."""
    url = f"{APPVIEW}/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "bridgeu-bench/0.1"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
            status = resp.status
    except urllib.error.HTTPError as e:
        return None, time.perf_counter() - t0, e.code
    except Exception:
        return None, time.perf_counter() - t0, -1
    dt = time.perf_counter() - t0
    if pace:
        time.sleep(pace)
    return body, dt, status


def summarize(latencies: list[float]) -> dict:
    if not latencies:
        return {"calls": 0}
    return {
        "calls": len(latencies),
        "total_s": round(sum(latencies), 3),
        "median_s": round(median(latencies), 3),
        "min_s": round(min(latencies), 3),
        "max_s": round(max(latencies), 3),
    }


def resolve_seeds(handles: list[str], pace: float) -> dict:
    """getProfile per handle -> did + graph sizes. The cheapest call; also the
    denominator for projecting a follower-graph crawl."""
    lat, out = [], {}
    for h in handles:
        body, dt, st = xrpc("app.bsky.actor.getProfile", {"actor": h}, pace)
        lat.append(dt)
        if body:
            out[h] = {
                "did": body.get("did"),
                "followers": body.get("followersCount"),
                "follows": body.get("followsCount"),
                "posts": body.get("postsCount"),
                "status": st,
            }
        else:
            out[h] = {"status": st}
    return {"timing": summarize(lat), "seeds": out}


def bench_graph(method: str, did: str, pages: int, pace: float) -> dict:
    """Paginate getFollowers / getFollows: measure accounts/sec, the unit that
    sets the cost of any follower-graph crawl."""
    lat, cursor, n_edges, page_sizes = [], None, 0, []
    for _ in range(pages):
        params = {"actor": did, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        body, dt, st = xrpc(method, params, pace)
        lat.append(dt)
        if not body:
            break
        key = "followers" if "Followers" in method else "follows"
        batch = body.get(key, [])
        n_edges += len(batch)
        page_sizes.append(len(batch))
        cursor = body.get("cursor")
        if not cursor or not batch:
            break
    t = summarize(lat)
    return {
        "timing": t,
        "edges": n_edges,
        "pages": len(page_sizes),
        "edges_per_s": round(n_edges / t["total_s"], 1) if t.get("total_s") else None,
    }


def pick_seed_posts(feed_uri: str, want: int, keyword: str | None, pace: float) -> tuple[list, dict]:
    """Source seed posts from a public feed generator (searchPosts is blocked
    unauth). Optional keyword = crude topic slice, to show topic scoping is a
    client-side filter here, not a server capability."""
    lat, posts, cursor = [], [], None
    while len(posts) < want:
        params = {"feed": feed_uri, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        body, dt, st = xrpc("app.bsky.feed.getFeed", params, pace)
        lat.append(dt)
        if not body:
            break
        for item in body.get("feed", []):
            p = item.get("post")
            if not p:
                continue
            text = (p.get("record") or {}).get("text", "")
            if keyword and keyword.lower() not in text.lower():
                continue
            posts.append(p)
        cursor = body.get("cursor")
        if not cursor:
            break
    return posts[:want], summarize(lat)


def walk_thread(node: dict, edges: list) -> None:
    """Collect (reply_author -> parent_author) reply edges from a getPostThread
    tree, depth-first."""
    post = node.get("post") or {}
    author = (post.get("author") or {}).get("did")
    parent_uri = ((post.get("record") or {}).get("reply") or {}).get("parent", {}).get("uri")
    parent_author = None
    if parent_uri and parent_uri.startswith("at://"):
        parent_author = parent_uri.removeprefix("at://").split("/", 1)[0]
    if author and parent_author:
        edges.append((author, parent_author))
    for child in node.get("replies", []) or []:
        walk_thread(child, edges)


def mention_edges(post: dict) -> list:
    """(author -> mentioned_did) from richtext mention facets in a post record."""
    author = (post.get("author") or {}).get("did")
    out = []
    for facet in (post.get("record") or {}).get("facets", []) or []:
        for feat in facet.get("features", []) or []:
            if feat.get("$type") == "app.bsky.richtext.facet#mention" and author:
                out.append((author, feat.get("did")))
    return out


def bench_reply_networks(posts: list, pace: float) -> dict:
    """getPostThread per seed post -> reply + mention edges. Reports the network
    built and per-call timing."""
    lat, reply_edges, ment_edges, threads = [], [], [], 0
    for p in posts:
        if (p.get("replyCount") or 0) == 0:
            ment_edges += mention_edges(p)  # still harvest mentions from the root
            continue
        body, dt, st = xrpc(
            "app.bsky.feed.getPostThread", {"uri": p["uri"], "depth": 6, "parentHeight": 0}, pace
        )
        lat.append(dt)
        if not body:
            continue
        threads += 1
        walk_thread(body.get("thread", {}), reply_edges)
        ment_edges += mention_edges(p)
    nodes = {n for e in reply_edges for n in e}
    return {
        "timing": summarize(lat),
        "threads_fetched": threads,
        "reply_edges": len(reply_edges),
        "reply_nodes": len(nodes),
        "mention_edges": len(ment_edges),
    }


def bench_likes(posts: list, pages: int, pace: float) -> dict:
    """getLikes per post -> (liker_did, post) engagement edges: the exact
    user-post pairs the CF factorization needs, here from the AppView rather
    than a firehose slice."""
    lat, n_likes, sampled = [], 0, 0
    top = sorted(posts, key=lambda p: p.get("likeCount") or 0, reverse=True)[:5]
    for p in top:
        cursor = None
        sampled += 1
        for _ in range(pages):
            params = {"uri": p["uri"], "limit": 100}
            if cursor:
                params["cursor"] = cursor
            body, dt, st = xrpc("app.bsky.feed.getLikes", params, pace)
            lat.append(dt)
            if not body:
                break
            n_likes += len(body.get("likes", []))
            cursor = body.get("cursor")
            if not cursor:
                break
    t = summarize(lat)
    return {
        "timing": t,
        "posts_sampled": sampled,
        "like_edges": n_likes,
        "likes_per_s": round(n_likes / t["total_s"], 1) if t.get("total_s") else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--handles", nargs="+", default=SEED_HANDLES)
    ap.add_argument("--graph-pages", type=int, default=3, help="pages/method for followers/follows")
    ap.add_argument("--feed", default=WHATS_HOT, help="public feed to source seed posts")
    ap.add_argument("--seed-posts", type=int, default=40)
    ap.add_argument("--keyword", default=None, help="client-side topic filter on seed-post text")
    ap.add_argument("--like-pages", type=int, default=2, help="pages/post for getLikes")
    ap.add_argument("--pace", type=float, default=0.05, help="seconds slept between calls")
    ap.add_argument("--out", default="../outputs/appview_bench.json")
    args = ap.parse_args()

    results: dict = {"appview": APPVIEW, "params": vars(args)}
    wall0 = time.perf_counter()

    print("[1/5] resolving seeds ...")
    results["seeds"] = resolve_seeds(args.handles, args.pace)
    dids = [v["did"] for v in results["seeds"]["seeds"].values() if v.get("did")]

    print("[2/5] follower graph (getFollowers / getFollows) ...")
    graph = {}
    if dids:
        graph["getFollows"] = bench_graph("app.bsky.graph.getFollows", dids[0], args.graph_pages, args.pace)
        graph["getFollowers"] = bench_graph("app.bsky.graph.getFollowers", dids[0], args.graph_pages, args.pace)
    results["follower_graph"] = graph

    print("[3/5] sourcing seed posts ...")
    posts, feed_timing = pick_seed_posts(args.feed, args.seed_posts, args.keyword, args.pace)
    results["seed_posts"] = {"timing": feed_timing, "n_posts": len(posts), "keyword": args.keyword}

    print("[4/5] reply + mention networks (getPostThread) ...")
    results["reply_networks"] = bench_reply_networks(posts, args.pace)

    print("[5/5] like edges (getLikes) ...")
    results["like_edges"] = bench_likes(posts, args.like_pages, args.pace)

    results["wall_clock_s"] = round(time.perf_counter() - wall0, 2)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}  (wall clock {results['wall_clock_s']}s)")


if __name__ == "__main__":
    main()
