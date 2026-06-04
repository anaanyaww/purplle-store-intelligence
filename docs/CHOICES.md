# CHOICES.md — Three Engineering Decisions

## Decision 1: Detection Model — YOLOv8n

**Options considered:**
- YOLOv8n (nano): 3.2M params, ~8–15fps on CPU, sufficient accuracy for person detection
- YOLOv8s (small): 2× slower, ~5% accuracy gain — not worth the cost
- RT-DETR: transformer-based, better occlusion handling, but requires GPU for real-time
- MediaPipe Pose: fast but limited to single-person or close-range; fails for groups
- GPT-4V / Claude Vision (VLM): tested for zone classification — too slow at 2–3s/frame; usable for spot checks but not frame-by-frame inference

**What AI suggested:** Use YOLOv8s for better accuracy on partial occlusion. I overrode this in favour of YOLOv8n. Rationale: the spec explicitly says detection need not be perfect — what matters is confidence calibration and edge case handling. YOLOv8n at 15fps gives continuous coverage; YOLOv8s at 7fps misses fast movement events (group entries, queue buildups). I did use a VLM (Claude) for one-off zone boundary validation when I couldn't determine the CAM_MAKEUP split point from specs alone — it analysed the store layout image and suggested the 40/60 horizontal split for FOH_CENTRAL vs MAKEUP_FLOOR.

**ByteTrack** (built into ultralytics): chosen over DeepSORT because it doesn't require appearance features for re-association — it uses IoU + kalman filter, which handles the partial occlusion cases in the billing clip well. Re-ID is handled separately via colour histograms, not embedded in the tracker.

Zone boundary calibration used Claude Vision on the first frame of 
CAM_MAKEUP. I initially estimated a 40/60 horizontal split for 
FOH_CENTRAL vs MAKEUP_FLOOR. Claude Vision responded:

"suggested_split_pct: 35/65, confidence: 0.82. The PMU/makeup 
station occupies roughly the leftmost 30-35% of the frame, with 
the transition to brand wall shelving beginning around the 35% mark. 
The right 65% is more cohesive as MAKEUP_FLOOR since the tiered brand 
gondolas (Lakmé, Faces Canada, Maybelline) dominate from that point."

I accepted this correction — 35/65 is more defensible given the 
visible fixture layout. This changed the zone boundary from 0.40 to 
0.35 in zone_mapper.py. The VLM identified a real fixture transition 
point I had estimated slightly conservatively.

Watching the footage confirmed staff wear all-black uniforms — clearly distinguishable from customers in CAM_SKINCARE and CAM_MAKEUP. This made the HSV colour histogram classifier viable as a primary signal rather than a fallback, which changed the staff detection architecture. The stockroom area had zero customers — only staff accessing inventory — which validated hardcoding is_staff=true for all stockroom detections without needing any classifier.

Watching CAM_ENTRY footage also revealed the camera captures Brigade Road foot traffic through the glass facade — a real-world complication not mentioned in the spec. Direction-of-travel detection reduces false ENTRY events but does not eliminate them. The 49 floor-camera visitors and 23 entry-camera events reflect two separate observation windows, not a detection error.

**What would change this decision:** If the store deployed GPU edge hardware (Jetson Orin), YOLOv8s or RT-DETR becomes viable and the accuracy gain on partial occlusion at the billing counter would justify the switch.

---

## Decision 2: Event Schema Design

**Options considered:**
- Flat schema (all fields at top level): simpler to query, but noisy for unused fields
- Nested metadata object: follows the spec exactly; queue_depth and sku_zone are only relevant for billing events — keeping them in metadata signals intent
- Separate event tables per type: over-engineered for one store, makes funnel queries complex

**Choice:** Spec-compliant schema with nested `metadata`. Key non-obvious decisions:

- `confidence` is never suppressed or rounded up — low-confidence detections are emitted with their real score. This is explicitly required by the spec and important for integrity checks.
- `zone_id` is NULL for ENTRY/EXIT/REENTRY — validated in Pydantic model with a `model_validator`. A reviewer running automated tests would catch zone_id on an ENTRY event.
- `session_seq` is ordinal within a visit session. Re-entries start a new session but keep the same `visitor_id`. The API's session table tracks `session_number` separately.

**What AI suggested:** Include a top-level `session_id` field in events. I evaluated this and decided against it — the spec schema doesn't include it, adding undocumented fields risks schema compliance failures in automated tests, and `visitor_id + session_number` in the DB is sufficient for funnel deduplication.

Watching the footage showed customers returning to products they were considering — not leaving the store and re-entering. The 8 REENTRY events in the final pipeline reflect genuine re-appearances after track loss (occlusion behind shelves). An earlier pipeline run produced 39 false REENTRYs caused by an aggressive Re-ID similarity threshold matching new customers to departed tracks. The fix — requiring a confirmed EXIT event before emitting REENTRY — corrected this. Track loss/recovery now emits ZONE_ENTER to continue the session rather than inflating REENTRY counts.

**What would change this decision:** If Purplle's existing data platform already consumed a specific event schema (Segment, Mixpanel), conforming to that rather than the spec schema would eliminate transformation layers downstream.

---

## Decision 3: API Architecture — FastAPI + SQLite

**Options considered:**
- FastAPI + PostgreSQL + Redis: production-grade but requires 3 services in Docker, complex setup, overkill for one store's data volume
- FastAPI + SQLite: zero-config, single-file DB, `docker compose up` works with one service, fully ACID-compliant, handles the analytics query patterns without issues at this scale
- Flask + in-memory dict: fastest to write, fails on restart, not production-aware
- DuckDB: excellent for analytics queries, but less mature async story

**Choice:** FastAPI + SQLite (`aiosqlite` for async). Justification:

- Single store, ~20 minutes of footage at 15fps processed every 5 frames ≈ ~2,400 frames per camera × 5 cameras ≈ ~12,000 frame samples. Even at 5 events per sample, that's ~60,000 events — well within SQLite's comfort zone.
- SQLite with proper indexes (added on `store_id`, `visitor_id`, `timestamp`, `event_type`) handles all the analytics queries in <10ms.
- `docker compose up` starts one container. No orchestration needed.

**At 40 live stores sending events in real time:** SQLite is the first failure. Concurrent ingest writes from 40 stores hit WAL contention limits at roughly 50–100 writes/second. Migration path: replace aiosqlite with asyncpg (PostgreSQL), add a Redis Streams buffer between /events/ingest and the DB writer, and partition the events table by store_id. Session computation moves from per-request SQL joins to materialized views refreshed every 30 seconds. This is documented but not implemented — single-store scope doesn't justify the complexity, and adding it would violate the `docker compose up` simplicity constraint.

**What AI suggested:** Use PostgreSQL from the start "for production correctness." I disagree for this context — the spec says `docker compose up` must start everything with no manual steps. Adding PostgreSQL initialization, user creation, and schema migrations to a Docker setup introduces failure modes that don't exist with SQLite. The pragmatic choice is SQLite now, PostgreSQL when the scale demands it, and document the decision clearly.