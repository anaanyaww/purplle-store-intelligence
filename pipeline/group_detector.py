"""
Post-processing script: adds group_id and group_size to ENTRY events.

Logic: ENTRY events on the same camera within a 3-second window share a group.
Each person still gets their own ENTRY event (spec requirement) but they share
a group_id in metadata.

Purplle's sample_events.jsonl confirmed they track group_id and group_size
internally, validating this approach.

Usage: python3 pipeline/group_detector.py data/events.jsonl
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List

GROUP_WINDOW_SECS = 3  # people entering within 3s = same group


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(
        ts.replace("Z", "+00:00").replace("+05:30", "+05:30")
    ).astimezone(timezone.utc)


def assign_groups(events: List[dict]) -> List[dict]:
    # Collect all ENTRY events per camera, sorted by timestamp
    entry_events = [
        (i, e) for i, e in enumerate(events)
        if e["event_type"] == "ENTRY" and not e.get("is_staff", False)
    ]

    # Group by camera_id, find co-entries within window
    by_camera: dict[str, List[tuple]] = defaultdict(list)
    for idx, evt in entry_events:
        by_camera[evt["camera_id"]].append((idx, evt))

    for cam_id, cam_entries in by_camera.items():
        # Sort by timestamp
        cam_entries.sort(key=lambda x: x[1]["timestamp"])

        i = 0
        while i < len(cam_entries):
            group_indices = [cam_entries[i][0]]
            t0 = parse_ts(cam_entries[i][1]["timestamp"])

            j = i + 1
            while j < len(cam_entries):
                tj = parse_ts(cam_entries[j][1]["timestamp"])
                if abs((tj - t0).total_seconds()) <= GROUP_WINDOW_SECS:
                    group_indices.append(cam_entries[j][0])
                    j += 1
                else:
                    break

            if len(group_indices) >= 2:
                # Assign shared group_id to all members
                anchor_visitor = events[group_indices[0]]["visitor_id"]
                group_id = f"GRP_{anchor_visitor[-6:]}"
                group_size = len(group_indices)

                for idx in group_indices:
                    events[idx].setdefault("metadata", {})
                    events[idx]["metadata"]["group_id"]   = group_id
                    events[idx]["metadata"]["group_size"] = group_size
            else:
                # Solo entry
                events[cam_entries[i][0]].setdefault("metadata", {})
                events[cam_entries[i][0]]["metadata"]["group_id"]   = None
                events[cam_entries[i][0]]["metadata"]["group_size"] = None

            i = j if j > i + 1 else i + 1

    return events


def add_zone_hotspots(events: List[dict]) -> List[dict]:
    """Add zone_hotspot_x/y to ZONE_ENTER events from bounding box centroids."""
    # These come from the detection bounding box centroid —
    # stored in metadata if available, otherwise set to None
    for evt in events:
        if evt["event_type"] == "ZONE_ENTER":
            meta = evt.setdefault("metadata", {})
            if "zone_hotspot_x" not in meta:
                meta["zone_hotspot_x"] = None
            if "zone_hotspot_y" not in meta:
                meta["zone_hotspot_y"] = None
    return events


def process_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)

    events = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    original_count = len(events)
    events = assign_groups(events)
    events = add_zone_hotspots(events)

    # Count groups added
    grouped = sum(
        1 for e in events
        if e["event_type"] == "ENTRY"
        and e.get("metadata", {}).get("group_id") is not None
    )

    with open(p, "w") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")

    print(f"Processed {original_count} events")
    print(f"Group members detected: {grouped} ENTRY events assigned group_id")
    print(f"Updated: {path}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/events.jsonl"
    process_file(path)