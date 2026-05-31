"""
Health endpoint: service status + per-camera feed lag.
STALE_FEED triggered if any camera has no events for >10 minutes
relative to the last recorded event (batch/replay-aware).
"""
import logging
from datetime import datetime, timezone
from typing import List

from .database import get_db, now_iso, STORE_ID
from .models import HealthResponse, StoreHealth, CameraFeedStatus

logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 10


async def get_health() -> HealthResponse:
    as_of    = now_iso()
    warnings: List[str] = []

    try:
        async with get_db() as db:
            cur = await db.execute(
                """SELECT camera_id, MAX(timestamp)
                   FROM events WHERE store_id=?
                   GROUP BY camera_id""",
                (STORE_ID,),
            )
            rows = await cur.fetchall()

            # Use max event timestamp as reference 'now' — correct for batch/replay mode.
            # Prevents false STALE alerts when comparing April footage to May wall clock.
            cur2 = await db.execute(
                "SELECT MAX(timestamp) FROM events WHERE store_id=?", (STORE_ID,)
            )
            max_row = await cur2.fetchone()

        if max_row and max_row[0]:
            try:
                ref = datetime.fromisoformat(max_row[0].replace("Z", "+00:00").replace("+05:30", "+05:30"))
                now_dt = ref.astimezone(timezone.utc) if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
            except Exception:
                now_dt = datetime.now(timezone.utc)
        else:
            now_dt = datetime.now(timezone.utc)

        camera_feeds: List[CameraFeedStatus] = []
        for cam_id, last_str in rows:
            if not last_str:
                camera_feeds.append(CameraFeedStatus(
                    camera_id=cam_id, last_event_at=None,
                    lag_minutes=None, status="NO_EVENTS"
                ))
                warnings.append(f"STALE_FEED: {cam_id} — no events recorded")
                continue

            try:
                ts = datetime.fromisoformat(last_str.replace("Z", "+00:00").replace("+05:30", "+05:30"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                lag_min = (now_dt - ts.astimezone(timezone.utc)).total_seconds() / 60
                status = "STALE" if lag_min > STALE_THRESHOLD_MINUTES else "OK"
                if status == "STALE":
                    warnings.append(f"STALE_FEED: {cam_id} — {int(lag_min)} min since last event")
                camera_feeds.append(CameraFeedStatus(
                    camera_id=cam_id,
                    last_event_at=last_str,
                    lag_minutes=round(lag_min, 1),
                    status=status,
                ))
            except Exception as e:
                logger.warning(f"Health check parse error for {cam_id}: {e}")
                camera_feeds.append(CameraFeedStatus(
                    camera_id=cam_id, last_event_at=last_str,
                    lag_minutes=None, status="NO_EVENTS"
                ))

        overall = "OK"
        if any(f.status == "STALE" for f in camera_feeds):
            overall = "DEGRADED"
        if not camera_feeds:
            overall = "DOWN"
            warnings.append("No camera feeds detected — pipeline may not have run yet")

        store_health = StoreHealth(store_id=STORE_ID, camera_feeds=camera_feeds)
        return HealthResponse(
            status=overall, as_of=as_of,
            stores=[store_health], warnings=warnings
        )

    except Exception as exc:
        logger.error(f"Health check DB error: {exc}")
        return HealthResponse(
            status="DOWN", as_of=as_of, stores=[],
            warnings=[f"Database unavailable: {type(exc).__name__}"]
        )
