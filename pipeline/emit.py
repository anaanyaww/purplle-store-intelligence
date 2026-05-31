"""
Event emitter: converts per-frame detections → structured JSONL events.
Maintains stateful track_id registry across frames for one camera session.
"""
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

STORE_ID        = "STORE_BLR_002"
DWELL_EMIT_MS   = 30_000   # emit ZONE_DWELL every 30 seconds of continuous presence
REENTRY_GAP_SEC = 60       # visitor gone >60s then reappears = re-entry candidate
REID_SIM_THRESH = 0.82     # cosine similarity threshold for re-ID


def _visitor_id() -> str:
    return f"VIS_{uuid.uuid4().hex[:6].upper()}"


class EventEmitter:
    def __init__(self, output_path: str, camera_id: str, base_ts: datetime):
        self.output_path = output_path
        self.camera_id   = camera_id
        self.base_ts     = base_ts
        self.store_id    = STORE_ID

        # track_id → state dict
        self._tracks: Dict[int, dict] = {}
        # track_ids seen in current frame
        self._active_this_frame: set = set()
        # departed tracks: visitor_id → {ts, histogram}
        self._gallery: List[dict] = []
        # visitor_ids that have a confirmed EXIT event
        # REENTRY is only valid if visitor previously exited the store
        # Track loss/recovery (occlusion, shelf obstruction) is NOT re-entry
        self._confirmed_exits: set = set()

        self._file = open(output_path, "a", buffering=1)  # line-buffered
        logger.info(f"Emitter: {camera_id} → {output_path}")

    # ------------------------------------------------------------------
    def process_frame(self, detections: List[Tuple],
                      frame: np.ndarray, frame_num: int, fps: float,
                      zone_fn, staff_detector) -> None:
        """
        detections: list of (track_id, bbox_xyxy, confidence)
        zone_fn: callable(camera_id, cx, cy, w, h) → zone_id
        """
        offset_ms = int((frame_num / fps) * 1000)
        ts = self.base_ts + timedelta(milliseconds=offset_ms)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S+05:30")

        fh, fw = frame.shape[:2]
        current_ids = set()

        for track_id, bbox, conf in detections:
            current_ids.add(track_id)
            x1, y1, x2, y2 = bbox
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            zone_id = zone_fn(self.camera_id, cx, cy, fw, fh)
            is_staff = staff_detector.is_staff(
                self.camera_id, track_id, frame, bbox, zone_id, dwell_delta_ms=67
            )

            if track_id not in self._tracks:
                # New track — check re-ID gallery first
                hist = _colour_hist(frame, bbox)
                matched_visitor = self._reid_lookup(hist, ts)

                # REENTRY requires a confirmed prior EXIT event.
                # Track loss/recovery (occlusion behind shelf, 2-5 min gap within clip)
                # is NOT a store re-entry — emit ZONE_ENTER to continue the session instead.
                if matched_visitor and matched_visitor in self._confirmed_exits:
                    visitor_id = matched_visitor
                    event_type = "REENTRY"
                elif matched_visitor:
                    # Re-ID match but no prior EXIT — track recovery, not re-entry
                    visitor_id = matched_visitor
                    event_type = None   # will emit ZONE_ENTER only
                else:
                    visitor_id = _visitor_id()
                    event_type = "ENTRY" if self.camera_id == "CAM_ENTRY" else None

                self._tracks[track_id] = {
                    "visitor_id":       visitor_id,
                    "zone_id":          zone_id,
                    "zone_entry_ts":    ts,
                    "last_dwell_ts":    ts,
                    "session_seq":      1,
                    "is_staff":         is_staff,
                    "history":          [(cy,)],
                    "hist":             hist,
                    "entry_ts":         ts,
                }

                if event_type == "ENTRY":
                    self._emit(event_type, visitor_id, ts_str, None,
                               0, is_staff, conf, 1)
                elif event_type == "REENTRY":
                    self._emit("REENTRY", visitor_id, ts_str, None,
                               0, is_staff, conf, 1)
                    self._emit("ZONE_ENTER", visitor_id, ts_str, zone_id,
                               0, is_staff, conf, 2)
                elif zone_id and zone_id != "ENTRY_EXIT":
                    self._emit("ZONE_ENTER", visitor_id, ts_str, zone_id,
                               0, is_staff, conf, 1)

            else:
                state = self._tracks[track_id]
                state["history"].append((cy,))
                if len(state["history"]) > 30:
                    state["history"] = state["history"][-30:]
                visitor_id = state["visitor_id"]
                is_staff   = state["is_staff"] or is_staff
                state["is_staff"] = is_staff
                seq = state["session_seq"]

                # Zone change
                if zone_id != state["zone_id"] and zone_id:
                    if state["zone_id"] and state["zone_id"] != "ENTRY_EXIT":
                        dwell = int((ts - state["zone_entry_ts"]).total_seconds() * 1000)
                        self._emit("ZONE_EXIT", visitor_id, ts_str,
                                   state["zone_id"], dwell, is_staff, conf, seq)
                    state["zone_id"] = zone_id
                    state["zone_entry_ts"] = ts
                    state["last_dwell_ts"] = ts
                    seq += 1
                    state["session_seq"] = seq
                    if zone_id != "ENTRY_EXIT":
                        self._emit("ZONE_ENTER", visitor_id, ts_str,
                                   zone_id, 0, is_staff, conf, seq)

                # Periodic ZONE_DWELL emit (every 30 s)
                if zone_id and zone_id != "ENTRY_EXIT":
                    since_dwell = (ts - state["last_dwell_ts"]).total_seconds() * 1000
                    if since_dwell >= DWELL_EMIT_MS:
                        self._emit("ZONE_DWELL", visitor_id, ts_str,
                                   zone_id, int(since_dwell), is_staff, conf, seq)
                        state["last_dwell_ts"] = ts

                # Billing events
                if zone_id == "CASH_COUNTER" and not is_staff:
                    if state["zone_id"] != "CASH_COUNTER":
                        self._emit("BILLING_QUEUE_JOIN", visitor_id, ts_str,
                                   zone_id, 0, is_staff, conf, seq,
                                   queue_depth=1)

        # Handle disappeared tracks (tracks in previous frame but not this one)
        departed = set(self._tracks.keys()) - current_ids
        for tid in departed:
            state = self._tracks.pop(tid)
            visitor_id = state["visitor_id"]
            is_staff   = state["is_staff"]
            ts_str_dep = ts.strftime("%Y-%m-%dT%H:%M:%S+05:30")

            if state["zone_id"] and state["zone_id"] != "ENTRY_EXIT":
                dwell = int((ts - state["zone_entry_ts"]).total_seconds() * 1000)
                self._emit("ZONE_EXIT", visitor_id, ts_str_dep,
                           state["zone_id"], dwell, is_staff, 0.5,
                           state["session_seq"])

            # Determine exit direction for entry camera
            if self.camera_id == "CAM_ENTRY":
                from .zone_mapper import is_entry_direction_inbound
                inbound = is_entry_direction_inbound(state["history"])
                if not inbound:
                    self._emit("EXIT", visitor_id, ts_str_dep, None,
                               0, is_staff, 0.5, state["session_seq"] + 1)
                    # Record confirmed exit — enables legitimate REENTRY detection
                    self._confirmed_exits.add(visitor_id)

            # Add to re-ID gallery (for re-entry detection)
            self._gallery.append({
                "visitor_id": visitor_id,
                "departed_ts": ts,
                "hist": state.get("hist"),
            })
            # Keep gallery small
            if len(self._gallery) > 100:
                self._gallery = self._gallery[-100:]

    # ------------------------------------------------------------------
    def _reid_lookup(self, hist, current_ts: datetime) -> Optional[str]:
        if hist is None:
            return None
        for entry in reversed(self._gallery):
            gap = (current_ts - entry["departed_ts"]).total_seconds()
            if gap < REENTRY_GAP_SEC or gap > 3600:
                continue
            if entry["hist"] is not None:
                sim = _cosine_sim(hist, entry["hist"])
                if sim >= REID_SIM_THRESH:
                    logger.debug(f"Re-ID match: {entry['visitor_id']} (sim={sim:.2f})")
                    return entry["visitor_id"]
        return None

    def _emit(self, event_type: str, visitor_id: str, ts: str,
              zone_id: Optional[str], dwell_ms: int,
              is_staff: bool, conf: float, seq: int,
              queue_depth: Optional[int] = None) -> None:
        event = {
            "event_id":   str(uuid.uuid4()),
            "store_id":   self.store_id,
            "camera_id":  self.camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp":  ts,
            "zone_id":    zone_id,
            "dwell_ms":   dwell_ms,
            "is_staff":   is_staff,
            "confidence": round(conf, 3),
            "metadata": {
                "queue_depth": queue_depth,
                "sku_zone":    None,
                "session_seq": seq,
            },
        }
        self._file.write(json.dumps(event) + "\n")

    def close(self):
        self._file.close()


# ------------------------------------------------------------------
# Helpers

def _colour_hist(frame: np.ndarray, bbox: np.ndarray) -> Optional[np.ndarray]:
    try:
        import cv2
        x1, y1, x2, y2 = (int(v) for v in bbox)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        hsv  = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [18, 16],
                            [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        return hist.flatten()
    except Exception:
        return None


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)