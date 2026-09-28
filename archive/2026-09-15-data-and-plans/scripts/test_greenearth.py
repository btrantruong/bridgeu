"""Smoke-test the Green Earth API end to end with an X-API-Key.

The key is read from the environment (GEA_KEY) and never written to disk, so
this script is safe to keep in the repo. Run:

    GEA_KEY=... uv run python test_greenearth.py

Probes, in order: health + describeFeedGenerator (no key), Skylight topic
search (the data-relevant endpoint), the candidate generators and ranking
models, then the POST pipeline generate -> rank -> diversify chained on live
outputs. Each call is timed; object schemas are reported by field name with
embeddings summarized (length only) so nothing huge is dumped.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.greenearth.social"
KEY = os.environ.get("GEA_KEY", "")


def call(method: str, path: str, params: dict | None = None, body: dict | None = None,
         use_key: bool = True) -> dict:
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "bridgeu-gea-test/0.1", "Accept": "application/json"}
    if use_key:
        headers["X-API-Key"] = KEY
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
            return {"ok": True, "status": resp.status, "s": round(time.perf_counter() - t0, 3),
                    "body": payload}
    except urllib.error.HTTPError as e:
        detail = e.read(600).decode("utf-8", "replace")
        return {"ok": False, "status": e.code, "s": round(time.perf_counter() - t0, 3),
                "error": detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": -1, "s": round(time.perf_counter() - t0, 3),
                "error": repr(e)}


def shape(obj) -> object:
    """Field names of a dict / first element of a list, embeddings summarized."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, list) and v and isinstance(v[0], (int, float)) and len(v) > 8:
                out[k] = f"<vector len={len(v)}>"
            elif isinstance(v, (dict, list)):
                out[k] = type(v).__name__
            else:
                out[k] = v if not isinstance(v, str) or len(v) < 60 else v[:57] + "..."
        return out
    if isinstance(obj, list):
        return [f"<list len={len(obj)}>"] + ([shape(obj[0])] if obj else [])
    return obj


def line(label: str, r: dict) -> None:
    tag = "OK " if r["ok"] else "ERR"
    print(f"[{tag}] {label:38s} http={r['status']:<4} {r['s']:>6}s")
    if not r["ok"]:
        print(f"        -> {r['error'][:200]}")


def main() -> None:
    if not KEY:
        raise SystemExit("set GEA_KEY in the environment")
    print(f"key: ...{KEY[-6:]}   base: {BASE}\n")
    results: dict = {}

    # --- unauthenticated sanity ---
    results["health"] = call("GET", "/health", use_key=False)
    line("GET /health", results["health"])
    results["describe"] = call("GET", "/xrpc/app.bsky.feed.describeFeedGenerator", use_key=False)
    line("GET describeFeedGenerator", results["describe"])

    # --- Skylight topic search (the data-relevant endpoint) ---
    for q in ["climate", "immigration", "election"]:
        r = call("GET", "/skylight/search", {"q": q, "size": 10})
        results[f"search:{q}"] = r
        line(f"GET /skylight/search q={q}", r)
        if r["ok"]:
            body = r["body"]
            items = body if isinstance(body, list) else body.get("results") or body.get("hits") or []
            print(f"        -> {len(items)} hits; item schema:")
            if items:
                for k, v in shape(items[0]).items():
                    print(f"           {k}: {v}")

    # --- catalogs ---
    results["generators"] = call("GET", "/candidates/generators")
    line("GET /candidates/generators", results["generators"])
    if results["generators"]["ok"]:
        print(f"        -> {json.dumps(results['generators']['body'])[:300]}")
    results["models"] = call("GET", "/rank/models")
    line("GET /rank/models", results["models"])
    if results["models"]["ok"]:
        print(f"        -> {json.dumps(results['models']['body'])[:300]}")
    results["feeds"] = call("GET", "/api/feeds")
    line("GET /api/feeds", results["feeds"])

    # --- POST pipeline: generate -> rank -> diversify (chained on live output) ---
    gens = results["generators"]["body"] if results["generators"]["ok"] else None
    gen_names = gens if isinstance(gens, list) else (gens or {}).get("generators") if gens else None
    user_did = "did:plc:z72i7hdynmk6r22z27h6tvur"  # bsky.app, a valid DID

    gen_body = {"user_did": user_did, "num_candidates": 10}
    if gen_names:
        gen_body["generators"] = gen_names[:2]
    r = call("POST", "/candidates/generate", body=gen_body)
    results["generate"] = r
    line("POST /candidates/generate", r)
    cands = []
    if r["ok"]:
        cands = r["body"] if isinstance(r["body"], list) else r["body"].get("candidates", [])
        print(f"        -> {len(cands)} candidates; schema:")
        if cands:
            for k, v in shape(cands[0]).items():
                print(f"           {k}: {v}")

    model_names = results["models"]["body"] if results["models"]["ok"] else None
    model_names = model_names if isinstance(model_names, list) else (model_names or {}).get("models")
    if cands:
        rank_body = {"candidates": cands, "user_did": user_did}
        if model_names:
            rank_body["models"] = model_names[:1]
        r = call("POST", "/rank/predict", body=rank_body)
        results["rank"] = r
        line("POST /rank/predict", r)
        r = call("POST", "/diversify", body={"candidates": cands})
        results["diversify"] = r
        line("POST /diversify", r)

    # persist run metadata WITHOUT the key or full embeddings
    slim = {k: {"ok": v["ok"], "status": v["status"], "s": v["s"]} for k, v in results.items()}
    with open("../outputs/greenearth_test.json", "w") as f:
        json.dump(slim, f, indent=2)
    print("\nwrote ../outputs/greenearth_test.json (timings only, no key, no embeddings)")


if __name__ == "__main__":
    main()
