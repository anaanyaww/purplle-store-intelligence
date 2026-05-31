#!/usr/bin/env python3
"""
Bridge script: reads events.jsonl produced by detection pipeline
and POSTs to /events/ingest in batches of 100.
"""
import json
import math
import sys
import requests
from pathlib import Path

BATCH_SIZE = 100


def ingest(events_file: str, api_url: str = "http://localhost:8000") -> None:
    path = Path(events_file)
    if not path.exists():
        print(f"ERROR: {events_file} not found. Run detection pipeline first.")
        sys.exit(1)

    events = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  Warning: bad JSON on line {i}: {e}")

    if not events:
        print("No events found. Nothing to ingest.")
        return

    n = len(events)
    n_batches = math.ceil(n / BATCH_SIZE)
    print(f"Sending {n} events in {n_batches} batches to {api_url}...")

    total_ingested = total_errors = 0
    for i in range(0, n, BATCH_SIZE):
        batch = events[i: i + BATCH_SIZE]
        b_num = i // BATCH_SIZE + 1
        try:
            r = requests.post(
                f"{api_url}/events/ingest",
                json={"events": batch},
                timeout=30,
            )
            r.raise_for_status()
            res = r.json()
            total_ingested += res.get("ingested", 0)
            total_errors   += len(res.get("errors", []))
            print(f"  Batch {b_num}/{n_batches}: ingested={res.get('ingested')} "
                  f"dupes={res.get('duplicates')} errors={len(res.get('errors',[]))}")
        except Exception as e:
            print(f"  ERROR batch {b_num}: {e}")

    print(f"\nDone. {total_ingested}/{n} ingested, {total_errors} errors.")


if __name__ == "__main__":
    f   = sys.argv[1] if len(sys.argv) > 1 else "data/events.jsonl"
    url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
    ingest(f, url)
