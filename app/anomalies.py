"""
Anomaly detection: queue spike, conversion drop, dead zone, stale feed.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List

from .database import get_db, now_iso
from .models import StoreAnomalies, Anomaly

logger = logging.getLogger(__name__)

QUEUE_SPIKE_THRESHOLD    = 3       # queue_depth > this triggers WARN
DEAD_ZONE_MINUTES        = 30      # no zone visits for this long → anomaly
STALE_FEED_MINUTES       = 10      # no events from any camera for this long
CONVERSION_DROP_THRESH   = 0.20    # >20% below baseline triggers WARN
BASELINE_CONVERSION_RATE = 0.35    # seeded 7-day baseline for Brigade Road.
                                   # In production this comes from a rolling
                                   # 7-day average table. Documented in DESIGN.md.


async def _get_reference_time(db, store_id: str) -> datetime:
    """
    For batch/replay mode: use max event timestamp as reference 'now'.
    Prevents false STALE_FEED and DEAD_ZONE alerts for historical footage.
    """
    cur = await db.execute(
        "SELECT MAX(timestamp) FROM events WHERE store_id=?", (store_id,)
    )
    row = await cur.fetchone()
    if row and row[0]:
        try:
            ts = row[0].replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


async def compute_anomalies(store_id: str) -> StoreAnomalies:
    as_of     = now_iso()
    anomalies: List[Anomaly] = []

    async with get_db() as db:
        now_dt = await _get_reference_time(db, store_id)

        # ── 1. Billing queue spike ───────────────────────────────────────────
        cur = await db.execute(
            """SELECT queue_depth, timestamp FROM events
               WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                     AND queue_depth IS NOT NULL
               ORDER BY timestamp DESC LIMIT 1""",
            (store_id,),
        )
        row = await cur.fetchone()
        if row and row[0] and int(row[0]) > QUEUE_SPIKE_THRESHOLD:
            depth = int(row[0])
            severity = "CRITICAL" if depth >= 6 else "WARN"
            anomalies.append(Anomaly(
                anomaly_type="BILLING_QUEUE_SPIKE",
                severity=severity,
                description=f"Billing queue depth is {depth} (threshold: {QUEUE_SPIKE_THRESHOLD})",
                suggested_action="Open additional billing counter or redirect staff to assist",
                detected_at=row[1],
                zone_id="CASH_COUNTER",
            ))

        # ── 2. Conversion drop vs seeded 7-day baseline ──────────────────────
        cur = await db.execute(
            "SELECT COUNT(*) FROM sessions WHERE store_id=?", (store_id,)
        )
        total_ses = (await cur.fetchone())[0] or 0

        cur = await db.execute(
            "SELECT COUNT(*) FROM sessions WHERE store_id=? AND is_converted=1",
            (store_id,),
        )
        converted = (await cur.fetchone())[0] or 0

        current_rate = (converted / total_ses) if total_ses > 0 else 0.0
        drop_threshold = BASELINE_CONVERSION_RATE * (1 - CONVERSION_DROP_THRESH)

        if current_rate < drop_threshold:
            anomalies.append(Anomaly(
                anomaly_type="CONVERSION_DROP",
                severity="WARN",
                description=(
                    f"Conversion rate {current_rate:.1%} is below "
                    f"{drop_threshold:.1%} "
                    f"(80% of seeded 7-day baseline {BASELINE_CONVERSION_RATE:.1%}). "
                    f"Note: CAM_BILLING has ~2 min coverage — "
                    f"full-day footage required for accurate conversion tracking."
                ),
                suggested_action=(
                    "Review billing zone staffing and check CAM_BILLING feed. "
                    "Ensure full-day camera coverage for reliable conversion metrics."
                ),
                detected_at=as_of,
            ))

        # ── 3. Dead zone — no visits in DEAD_ZONE_MINUTES ──────────────────
        cur = await db.execute(
            """SELECT zone_id, MAX(timestamp) as last_visit
               FROM events
               WHERE store_id=? AND is_staff=0 AND zone_id IS NOT NULL
                     AND event_type IN ('ZONE_ENTER','ZONE_DWELL')
               GROUP BY zone_id""",
            (store_id,),
        )
        rows = await cur.fetchall()
        seen_zones = {r[0]: r[1] for r in rows}

        cur = await db.execute(
            "SELECT MIN(timestamp) FROM events WHERE store_id=?", (store_id,)
        )
        first_event_row = await cur.fetchone()
        has_data = first_event_row and first_event_row[0]

        if has_data:
            customer_zones = ["SKINCARE_WALL", "MAKEUP_FLOOR", "FOH_CENTRAL",
                              "CASH_COUNTER", "ACCESSORIES"]
            for zone in customer_zones:
                last_str = seen_zones.get(zone)
                if last_str is None:
                    anomalies.append(Anomaly(
                        anomaly_type="DEAD_ZONE",
                        severity="INFO",
                        description=f"Zone {zone} has had no customer visits recorded",
                        suggested_action=f"Check camera coverage for {zone} or verify product placement",
                        detected_at=as_of,
                        zone_id=zone,
                    ))
                else:
                    try:
                        last_dt = datetime.fromisoformat(
                            last_str.replace("Z", "+00:00").replace("+05:30", "+05:30")
                        )
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        lag_min = (now_dt - last_dt.astimezone(timezone.utc)).total_seconds() / 60
                        if lag_min > DEAD_ZONE_MINUTES:
                            anomalies.append(Anomaly(
                                anomaly_type="DEAD_ZONE",
                                severity="WARN",
                                description=f"No visits in {zone} for {int(lag_min)} minutes",
                                suggested_action=(
                                    "Check for display issues or send staff to "
                                    "re-engage customers in this zone"
                                ),
                                detected_at=as_of,
                                zone_id=zone,
                            ))
                    except Exception:
                        pass

        # ── 4. Stale feed ────────────────────────────────────────────────────
        cur = await db.execute(
            """SELECT camera_id, MAX(timestamp) FROM events
               WHERE store_id=?
               GROUP BY camera_id""",
            (store_id,),
        )
        rows = await cur.fetchall()
        for cam_id, last_str in rows:
            if not last_str:
                continue
            try:
                last_dt = datetime.fromisoformat(
                    last_str.replace("Z", "+00:00").replace("+05:30", "+05:30")
                )
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                lag = (now_dt - last_dt.astimezone(timezone.utc)).total_seconds() / 60
                if lag > STALE_FEED_MINUTES:
                    anomalies.append(Anomaly(
                        anomaly_type="STALE_FEED",
                        severity="WARN",
                        description=f"No events from {cam_id} for {int(lag)} minutes",
                        suggested_action=(
                            f"Check camera connectivity and pipeline status for {cam_id}"
                        ),
                        detected_at=as_of,
                    ))
            except Exception:
                pass

    return StoreAnomalies(store_id=store_id, as_of=as_of, anomalies=anomalies)