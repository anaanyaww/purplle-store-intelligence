"""
SQLite database setup, schema, and POS data loader.
All DB operations are async via aiosqlite.
"""
import aiosqlite
import csv
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/store_intelligence.db")
POS_CSV_PATH = os.getenv("POS_CSV_PATH", "data/pos_transactions.csv")
STORE_LAYOUT_PATH = os.getenv("STORE_LAYOUT_PATH", "data/store_layout.json")

STORE_ID = "STORE_BLR_002"
INTERNAL_STORE_ID = "ST1008"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    store_id      TEXT NOT NULL,
    camera_id     TEXT NOT NULL,
    visitor_id    TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    zone_id       TEXT,
    dwell_ms      INTEGER DEFAULT 0,
    is_staff      INTEGER DEFAULT 0,
    confidence    REAL,
    queue_depth   INTEGER,
    sku_zone      TEXT,
    session_seq   INTEGER DEFAULT 0,
    ingested_at   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_evt_store    ON events(store_id);
CREATE INDEX IF NOT EXISTS idx_evt_visitor  ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_evt_ts       ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_evt_type     ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_evt_zone     ON events(zone_id);
CREATE INDEX IF NOT EXISTS idx_evt_staff    ON events(is_staff);

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    visitor_id          TEXT NOT NULL,
    store_id            TEXT NOT NULL,
    entry_time          TEXT,
    exit_time           TEXT,
    session_number      INTEGER DEFAULT 1,
    is_converted        INTEGER DEFAULT 0,
    billing_zone_entry  TEXT,
    conversion_time     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ses_visitor ON sessions(visitor_id, store_id);
CREATE INDEX IF NOT EXISTS idx_ses_store   ON sessions(store_id);

CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id       TEXT PRIMARY KEY,
    store_id             TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    basket_value_inr     REAL DEFAULT 0,
    order_id             TEXT,
    is_correlated        INTEGER DEFAULT 0,
    correlated_session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_pos_store ON pos_transactions(store_id);
CREATE INDEX IF NOT EXISTS idx_pos_ts    ON pos_transactions(timestamp);
"""


async def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info(f"Database initialised at {DB_PATH}")


async def load_pos_data() -> None:
    """
    Load POS transactions from CSV. Idempotent — uses INSERT OR IGNORE.
    Maps internal store ID ST1008 → STORE_BLR_002.
    Date format in CSV: DD-MM-YYYY  Time: HH:MM:SS (IST, UTC+5:30)
    """
    pos_path = Path(POS_CSV_PATH)
    if not pos_path.exists():
        logger.warning(f"POS CSV not found at {POS_CSV_PATH} — skipping")
        return

    loaded = skipped = 0
    async with aiosqlite.connect(DB_PATH) as db:
        with open(pos_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            seen_orders: set[str] = set()

            for row in reader:
                raw_store = row.get("store_id", "").strip()
                if raw_store not in (INTERNAL_STORE_ID, STORE_ID):
                    skipped += 1
                    continue

                order_id = str(row.get("order_id", "")).strip()
                invoice  = str(row.get("invoice_number", "")).strip()
                txn_id   = invoice or order_id
                if not txn_id or txn_id in seen_orders:
                    skipped += 1
                    continue
                seen_orders.add(txn_id)

                try:
                    date_str = row["order_date"].strip()   # DD-MM-YYYY
                    time_str = row["order_time"].strip()   # HH:MM:SS
                    dt_ist = datetime.strptime(
                        f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S"
                    )
                    # Store as IST string — correlation uses string comparison
                    # within same timezone frame as detection pipeline
                    ts_str = dt_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30")
                except (ValueError, KeyError) as e:
                    logger.debug(f"Bad POS row date parse: {e}")
                    skipped += 1
                    continue

                try:
                    basket = float(row.get("total_amount", 0) or 0)
                except ValueError:
                    basket = 0.0

                try:
                    await db.execute(
                        """INSERT OR IGNORE INTO pos_transactions
                           (transaction_id, store_id, timestamp, basket_value_inr, order_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (txn_id, STORE_ID, ts_str, basket, order_id),
                    )
                    loaded += 1
                except Exception as e:
                    logger.debug(f"POS insert error: {e}")
                    skipped += 1

        await db.commit()
    logger.info(f"POS data loaded: {loaded} transactions ({skipped} skipped/duplicate)")


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Context manager for database connections with automatic rollback on error."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
