"""
Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase

Entry and Zone Visit both use floor camera visitor_ids (consistent
population from 20:10–20:15 IST). CAM_ENTRY (12:00–12:04) is a
separate cohort and is NOT used as the funnel base — combining
incompatible time windows produces incoherent drop-off percentages.

Result: Entry=49, Zone Visit=49 (100% — all floor detections are
zone visits by definition), Billing=0, Purchase=0.
Story: everyone who entered browsed; nobody reached billing in
the observation window (CAM_BILLING ~2 min coverage).
"""
import logging
from .database import get_db, now_iso
from .models import StoreFunnel, FunnelStage

logger = logging.getLogger(__name__)

BILLING_MIN_SESSIONS = 5
EXCLUDE_ZONES = ('ENTRY_EXIT', 'BACKROOM')


async def compute_funnel(store_id: str) -> StoreFunnel:
    as_of = now_iso()

    async with get_db() as db:
        # Stage 1 + 2: floor camera visitors = consistent base population
        # All floor-camera detections are zone visits by definition —
        # you cannot appear on CAM_SKINCARE without being in SKINCARE_WALL.
        cur = await db.execute(
            f"""SELECT COUNT(DISTINCT visitor_id)
               FROM events
               WHERE store_id=? AND is_staff=0
                     AND event_type='ZONE_ENTER'
                     AND zone_id NOT IN {EXCLUDE_ZONES}""",
            (store_id,),
        )
        floor_visitors = (await cur.fetchone())[0] or 0

        # Stage 3: billing queue
        cur = await db.execute(
            "SELECT COUNT(*) FROM sessions WHERE store_id=? AND billing_zone_entry IS NOT NULL",
            (store_id,),
        )
        billing_sessions = (await cur.fetchone())[0] or 0

        # Stage 4: converted
        cur = await db.execute(
            "SELECT COUNT(*) FROM sessions WHERE store_id=? AND is_converted=1",
            (store_id,),
        )
        purchased = (await cur.fetchone())[0] or 0

    def drop_pct(current: int, previous: int) -> float:
        if previous == 0:
            return 0.0
        return round(max((1 - current / previous) * 100, 0.0), 1)

    billing_confidence = (
        "low — CAM_BILLING coverage ~2 min (20:11–20:13 IST). "
        "Billing queue events require visitor to be in CASH_COUNTER "
        "zone within this narrow window."
        if billing_sessions < BILLING_MIN_SESSIONS else None
    )

    note = (
        "Entry and Zone Visit both count floor-camera visitor_ids "
        "(CAM_SKINCARE, CAM_MAKEUP, CAM_BILLING, 20:10–20:15 IST). "
        "CAM_ENTRY (12:00–12:04) is a separate visitor cohort reported "
        "in /metrics as entry_camera_events. "
        "Zone Visit = 100% because all floor-camera detections are "
        "zone visits by definition."
    )

    stages = [
        FunnelStage(stage="Entry",
                    count=floor_visitors,
                    drop_off_pct=0.0),
        FunnelStage(stage="Zone Visit",
                    count=floor_visitors,
                    drop_off_pct=0.0),
        FunnelStage(stage="Billing Queue",
                    count=billing_sessions,
                    drop_off_pct=drop_pct(billing_sessions, floor_visitors),
                    data_confidence=billing_confidence),
        FunnelStage(stage="Purchase",
                    count=purchased,
                    drop_off_pct=drop_pct(purchased, billing_sessions),
                    data_confidence=billing_confidence),
    ]

    return StoreFunnel(store_id=store_id, as_of=as_of, stages=stages, note=note)