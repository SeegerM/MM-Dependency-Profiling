"""Runs the ER'25 reference-detection framework and scores it against the UniBench IND oracle.

The framework is the published implementation of Hausler et al. (ER 2025) in
upstream/ref-seeker-extractor, driven through its HTTP API on its shipped UniBench containers.
DATA_SIZE=0.01 gives the same row counts as our export (customer 99, invoice 1422, vendor 66,
Order 1422, Person 99, Tag 90). The framework validates inclusions by equality only, so only exact
IND discovery is comparable.

usage: er25_baseline.py [--api http://localhost:8001] [--oracle <unibench_oracle.json>]
                        [--out <results.json>]
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

ENTRIES = [
    {"uri": "mongodb://mongodb:27017/unibench", "user": "", "password": ""},
    {"uri": "postgresql://postgres:5432/unibench", "user": "postgres", "password": "root"},
    {"uri": "neo4j://neo4j:7687", "user": "neo4j", "password": "neo4jpassword"},
]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


def call(url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def run(api):
    """Submit the extraction and block until the framework reports it finished."""
    job = call(f"{api}/extract", {"entries": ENTRIES})["job_id"]
    print(f"job {job} submitted")
    started = time.time()
    while True:
        state = call(f"{api}/jobs/{job}")["status"]
        elapsed = time.time() - started
        if state == "finished":
            print(f"finished after {elapsed:.0f}s")
            return call(f"{api}/jobs/{job}/results", timeout=300)
        if state == "aborted":
            raise SystemExit(f"job aborted after {elapsed:.0f}s")
        if elapsed > 3600:
            raise SystemExit(f"job still {state} after an hour")
        time.sleep(10)


def main():
    api = flag("api", "http://localhost:8001")
    out = Path(flag("out", str(paths.WORK_ROOT / "er25/unibench-results.json")))
    out.parent.mkdir(parents=True, exist_ok=True)

    results = run(api)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"written to {out}")

    payload = results[0] if isinstance(results, list) else results
    for key in payload:
        value = payload[key]
        size = len(value) if isinstance(value, (list, dict)) else value
        print(f"  {key}: {size}")


if __name__ == "__main__":
    main()
