"""Zone heatmap: visit frequency + avg dwell, normalised 0-100.
Excludes ENTRY_EXIT and BACKROOM from customer-facing zone metrics.
"""
import json
import logging
from typing import List, Dict

from .database import get_db, now_iso, STORE_LAYOUT_PATH
from .models import StoreHeatmap, ZoneHeatmap

logger = logging.getLogger(__name__)

MIN_SESSIONS_FOR_CONFIDENCE = 20
EXCLUDE_ZONES = ('ENTRY_EXIT', 'BACKROOM')


def _load_zone_names() -> Dict[str, str]:
    try:
        with open(STORE_LAYOUT_PATH) as f:
            layout = json.load(f)
        return {z["zone_id"]: z["zone_name"] for z in layout.get("zones", [])}
    except Exception as e:
        logger.warning(f"Could not load zone names: {e}")
        return {}


async def compute_heatmap(store_id: str) -> StoreHeatmap:
    as_of      = now_iso()
    zone_names = _load_zone_names()

    async with get_db() as db:
        cur = await db.execute(
            f"""SELECT
                  zone_id,
                  COUNT(DISTINCT visitor_id) AS visit_count,
                  AVG(
                    CASE
                      WHEN exit_ts IS NOT NULL
                        THEN (julianday(exit_ts) - julianday(enter_ts)) * 86400000
                      WHEN dwell_ms > 0 THEN dwell_ms
                      ELSE NULL
                    END
                  ) AS avg_dwell
               FROM (
                 SELECT
                   e1.zone_id,
                   e1.visitor_id,
                   e1.timestamp  AS enter_ts,
                   e1.dwell_ms,
                   (SELECT MIN(e2.timestamp)
                    FROM events e2
                    WHERE e2.visitor_id  = e1.visitor_id
                      AND e2.zone_id     = e1.zone_id
                      AND e2.event_type  = 'ZONE_EXIT'
                      AND e2.timestamp   > e1.timestamp
                      AND e2.store_id    = e1.store_id
                   ) AS exit_ts
                 FROM events e1
                 WHERE e1.store_id   = ?
                   AND e1.is_staff   = 0
                   AND e1.event_type = 'ZONE_ENTER'
                   AND e1.zone_id NOT IN {EXCLUDE_ZONES}
               )
               GROUP BY zone_id""",
            (store_id,),
        )
        rows = await cur.fetchall()

    if not rows:
        return StoreHeatmap(store_id=store_id, as_of=as_of, zones=[])

    counts = [r[1] for r in rows]
    max_count = max(counts) if counts else 1

    zones: List[ZoneHeatmap] = []
    for row in rows:
        zone_id    = row[0]
        visit_freq = row[1]
        avg_dwell  = round(row[2] or 0, 1)
        normalised = round((visit_freq / max_count) * 100, 1)
        confidence = visit_freq >= MIN_SESSIONS_FOR_CONFIDENCE

        zones.append(ZoneHeatmap(
            zone_id=zone_id,
            zone_name=zone_names.get(zone_id, zone_id),
            visit_frequency=visit_freq,
            avg_dwell_ms=avg_dwell,
            normalized_score=normalised,
            data_confidence=confidence,
        ))

    zones.sort(key=lambda z: z.normalized_score, reverse=True)
    return StoreHeatmap(store_id=store_id, as_of=as_of, zones=zones)
