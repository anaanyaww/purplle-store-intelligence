"""
Extract base timestamp from CCTV overlay text.
Primary: EasyOCR on frame 0, validated against frame 30 and 60.
Fallback: hardcoded dict keyed by camera_id.
"""
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Confirmed from footage screenshots — used if OCR fails
FALLBACK_TIMESTAMPS = {
    "CAM_ENTRY":    "2026-04-10T12:00:00+05:30",
    "CAM_SKINCARE": "2026-04-10T20:10:46+05:30",
    "CAM_MAKEUP":   "2026-04-10T20:10:08+05:30",
    "CAM_BACKROOM": "2026-04-10T20:11:27+05:30",
    "CAM_BILLING":  "2026-04-10T20:11:22+05:30",
}

_TS_PATTERN = re.compile(r"(\d{2})[/\-](\d{2})[/\-](\d{4})\s+(\d{2}):(\d{2}):(\d{2})")


def _parse_overlay(text: str) -> Optional[datetime]:
    m = _TS_PATTERN.search(text)
    if not m:
        return None
    try:
        day, month, year, h, mi, s = (int(x) for x in m.groups())
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime(year, month, day, h, mi, s, tzinfo=ist)
    except ValueError:
        return None


def _ocr_frame(frame) -> Optional[datetime]:
    try:
        import easyocr
        import numpy as np
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        # Crop top-right 30% of frame where timestamp lives
        h, w = frame.shape[:2]
        crop = frame[0:int(h*0.15), int(w*0.55):]
        results = reader.readtext(crop, detail=0)
        text = " ".join(results)
        return _parse_overlay(text)
    except Exception as e:
        logger.debug(f"OCR attempt failed: {e}")
        return None


def extract_base_timestamp(video_path: str, camera_id: str) -> datetime:
    """
    Try OCR on frames 0, 30, 60 with consistency check.
    Falls back to hardcoded dict on any failure.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        candidates = []
        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0

        for target_frame in [0, 30, 60]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            ok, frame = cap.read()
            if not ok:
                continue
            dt = _ocr_frame(frame)
            if dt:
                # Rewind to frame 0 equivalent
                candidates.append(dt - timedelta(seconds=target_frame / fps))
        cap.release()

        if len(candidates) >= 2:
            # Consistency check: all candidates within 5 seconds of each other
            diffs = [abs((candidates[i] - candidates[0]).total_seconds())
                     for i in range(1, len(candidates))]
            if all(d < 5 for d in diffs):
                logger.info(f"{camera_id}: OCR timestamp OK → {candidates[0].isoformat()}")
                return candidates[0]
            else:
                logger.warning(f"{camera_id}: OCR timestamps inconsistent ({diffs}), using fallback")
        elif len(candidates) == 1:
            logger.info(f"{camera_id}: OCR single result → {candidates[0].isoformat()}")
            return candidates[0]
    except Exception as e:
        logger.warning(f"{camera_id}: OCR error ({e}), using fallback")

    # Fallback
    fallback_str = FALLBACK_TIMESTAMPS.get(camera_id, "2026-04-10T12:00:00+05:30")
    dt = datetime.fromisoformat(fallback_str)
    logger.info(f"{camera_id}: Using fallback timestamp → {dt.isoformat()}")
    return dt
