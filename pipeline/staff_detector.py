"""
Staff detection — two strategies:

1. Structural: CAM_BACKROOM → always staff (backroom = staff-only area).
2. Colour-based: Staff wear all-black uniforms (confirmed from footage).
   Crop the person bounding box, convert to HSV, check if >60% of pixels
   have low Value (dark) — threshold V < 60 in HSV(0-179, 0-255, 0-255).
3. Behavioural: track_ids present for >90 cumulative minutes across all
   cameras, traversing ≥3 distinct zones → flagged as staff.

Strategy priority: Structural → Colour → Behavioural → Customer.
"""
import logging
import numpy as np
from collections import defaultdict
from typing import Dict, Set

logger = logging.getLogger(__name__)

# HSV thresholds for black clothing detection
BLACK_V_MAX   = 60    # max HSV Value for "black" pixels
BLACK_SAT_MAX = 80    # also accept low-saturation dark (very dark grey)
BLACK_RATIO   = 0.55  # >55% dark pixels in torso crop = staff candidate

# Behavioural thresholds
STAFF_DWELL_THRESHOLD_MS  = 90 * 60 * 1000   # 90 minutes
STAFF_ZONE_COUNT_THRESHOLD = 3


class StaffDetector:
    def __init__(self):
        # track_id → cumulative dwell ms
        self._dwell: Dict[int, int] = defaultdict(int)
        # track_id → set of zones seen
        self._zones: Dict[int, Set[str]] = defaultdict(set)
        # track_id → confirmed staff
        self._confirmed: Dict[int, bool] = {}

    def is_staff(self, camera_id: str, track_id: int,
                 frame: np.ndarray, bbox: np.ndarray,
                 zone_id: str = "", dwell_delta_ms: int = 0) -> bool:
        """
        Returns True if this detection is a staff member.
        """
        if track_id in self._confirmed:
            return self._confirmed[track_id]

        # 1. Structural: backroom camera = always staff
        if camera_id == "CAM_BACKROOM":
            self._confirmed[track_id] = True
            return True

        # 2. Colour-based: check for black uniform
        if _has_black_uniform(frame, bbox):
            self._confirmed[track_id] = True
            logger.debug(f"Track {track_id} flagged staff by black uniform")
            return True

        # 3. Behavioural: accumulate dwell + zones
        self._dwell[track_id] += dwell_delta_ms
        if zone_id:
            self._zones[track_id].add(zone_id)

        if (self._dwell[track_id] >= STAFF_DWELL_THRESHOLD_MS and
                len(self._zones[track_id]) >= STAFF_ZONE_COUNT_THRESHOLD):
            self._confirmed[track_id] = True
            logger.debug(f"Track {track_id} flagged staff by behaviour "
                         f"(dwell={self._dwell[track_id]//60000}min, "
                         f"zones={self._zones[track_id]})")
            return True

        return False


def _has_black_uniform(frame: np.ndarray, bbox: np.ndarray) -> bool:
    """Check if the torso region of a bounding box contains mostly black pixels."""
    try:
        import cv2
        x1, y1, x2, y2 = (int(v) for v in bbox)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 - x1 < 10 or y2 - y1 < 10:
            return False

        # Focus on torso: middle 60% vertically, middle 80% horizontally
        box_h = y2 - y1
        box_w = x2 - x1
        ty1 = y1 + int(box_h * 0.20)
        ty2 = y1 + int(box_h * 0.80)
        tx1 = x1 + int(box_w * 0.10)
        tx2 = x1 + int(box_w * 0.90)

        crop = frame[ty1:ty2, tx1:tx2]
        if crop.size == 0:
            return False

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        v_channel = hsv[:, :, 2]
        s_channel = hsv[:, :, 1]

        dark_mask = (v_channel < BLACK_V_MAX) | \
                    ((v_channel < 90) & (s_channel < BLACK_SAT_MAX))
        dark_ratio = np.sum(dark_mask) / dark_mask.size
        return dark_ratio > BLACK_RATIO

    except Exception as e:
        logger.debug(f"Staff colour check error: {e}")
        return False
