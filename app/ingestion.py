"""
Event ingestion logic.
- Idempotent by event_id (INSERT OR IGNORE)
- Partial success: one bad event does not fail the batch
- Updates sessions table on ENTRY / EXIT / REENTRY
- Correlates BILLING_QUEUE_JOIN with POS transactions
"""
import logging
import uuid
from typing import List

import aiosqlite

from .database import get_db, now_iso, STORE_ID
from .models import StoreEvent, IngestResponse, IngestError

logger = logging.getLogger(__name__)


async def ingest_events_batch(events: List[StoreEvent]) -> IngestResponse:
    ingested = duplicates = 0
    errors: List[IngestError] = []

    async with get_db() as db:
        for event in events:
            try:
                # Idempotency check
                cur = await db.execute(
                    "SELECT 1 FROM events WHERE event_id = ?", (event.event_id,)
                )
                if await cur.fetchone():
                    duplicates += 1
                    continue

                await db.execute(
                    """INSERT INTO events
                       (event_id, store_id, camera_id, visitor_id, event_type,
                        timestamp, zone_id, dwell_ms, is_staff, confidence,
                        queue_depth, sku_zone, session_seq)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.event_type,
                        event.timestamp,
                        event.zone_id,
                        event.dwell_ms,
                        1 if event.is_staff else 0,
                        event.confidence,
                        event.metadata.queue_depth,
                        event.metadata.sku_zone,
                        event.metadata.session_seq,
                    ),
                )

                if not event.is_staff:
                    await _update_session(db, event)

                ingested += 1

            except Exception as exc:
                logger.warning(f"Ingest error for {event.event_id}: {exc}")
                errors.append(IngestError(event_id=event.event_id, reason=str(exc)))

        await db.commit()

    logger.info(
        f"Batch complete: ingested={ingested} duplicates={duplicates} errors={len(errors)}"
    )
    return IngestResponse(ingested=ingested, duplicates=duplicates, errors=errors)


async def _update_session(db: aiosqlite.Connection, event: StoreEvent) -> None:
    vid   = event.visitor_id
    sid   = event.store_id
    ts    = event.timestamp

    if event.event_type == "ENTRY":
        cur = await db.execute(
            "SELECT COALESCE(MAX(session_number), 0) FROM sessions WHERE visitor_id=? AND store_id=?",
            (vid, sid),
        )
        row = await cur.fetchone()
        next_num   = (row[0] if row else 0) + 1
        session_id = f"SES_{uuid.uuid4().hex[:8].upper()}"
        await db.execute(
            """INSERT OR IGNORE INTO sessions
               (session_id, visitor_id, store_id, entry_time, session_number)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, vid, sid, ts, next_num),
        )

    elif event.event_type == "EXIT":
        # Close most recent open session for this visitor
        cur = await db.execute(
            """SELECT session_id FROM sessions
               WHERE visitor_id=? AND store_id=? AND exit_time IS NULL
               ORDER BY entry_time DESC LIMIT 1""",
            (vid, sid),
        )
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE sessions SET exit_time=? WHERE session_id=?",
                (ts, row[0]),
            )

    elif event.event_type == "REENTRY":
        cur = await db.execute(
            "SELECT COALESCE(MAX(session_number), 0) FROM sessions WHERE visitor_id=? AND store_id=?",
            (vid, sid),
        )
        row = await cur.fetchone()
        next_num   = (row[0] if row else 0) + 1
        session_id = f"SES_{uuid.uuid4().hex[:8].upper()}"
        await db.execute(
            """INSERT INTO sessions
               (session_id, visitor_id, store_id, entry_time, session_number)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, vid, sid, ts, next_num),
        )

    elif event.event_type == "BILLING_QUEUE_JOIN":
        # Note billing zone entry for POS correlation
        cur = await db.execute(
            """SELECT session_id FROM sessions
               WHERE visitor_id=? AND store_id=? AND exit_time IS NULL
               ORDER BY entry_time DESC LIMIT 1""",
            (vid, sid),
        )
        row = await cur.fetchone()
        if row:
            session_id = row[0]
            await db.execute(
                "UPDATE sessions SET billing_zone_entry=? WHERE session_id=?",
                (ts, session_id),
            )
            await _correlate_pos(db, session_id, ts, sid)

    elif event.event_type == "BILLING_QUEUE_ABANDON":
        # Visitor left billing without purchasing — no conversion
        pass


async def _correlate_pos(
    db: aiosqlite.Connection,
    session_id: str,
    billing_entry_ts: str,
    store_id: str,
) -> None:
    """
    A visitor in the billing zone in the 5-minute window before a POS transaction
    counts as a converted visitor for that session.
    Both timestamps are in IST (+05:30) format from the pipeline.
    """
    try:
        cur = await db.execute(
            """SELECT transaction_id FROM pos_transactions
               WHERE store_id = ?
               AND is_correlated = 0
               AND timestamp >= ?
               AND timestamp <= datetime(?, '+5 minutes')
               ORDER BY timestamp ASC
               LIMIT 1""",
            (store_id, billing_entry_ts, billing_entry_ts),
        )
        row = await cur.fetchone()
        if row:
            txn_id = row[0]
            await db.execute(
                "UPDATE sessions SET is_converted=1, conversion_time=? WHERE session_id=?",
                (billing_entry_ts, session_id),
            )
            await db.execute(
                """UPDATE pos_transactions
                   SET is_correlated=1, correlated_session_id=?
                   WHERE transaction_id=?""",
                (session_id, txn_id),
            )
            logger.debug(f"POS correlated: session={session_id} txn={txn_id}")
    except Exception as exc:
        logger.debug(f"POS correlation skipped: {exc}")
