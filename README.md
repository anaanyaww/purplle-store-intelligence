# Store Intelligence System — Brigade Road, Bangalore
**Purplle Tech Challenge 2026 — Round 2**

AI-powered retail analytics from raw CCTV footage. Detects people, tracks movement, and computes live store metrics via a production-ready REST API.

## Documentation
- [Architecture & AI-Assisted Decisions](docs/DESIGN.md)
- [Engineering Decisions & Trade-offs](docs/CHOICES.md)

## Setup (5 commands)

```bash
# 1. Clone and enter
git clone <your-repo-url> store-intelligence && cd store-intelligence

# 2. Copy your data files (not included in repo)
cp /path/to/pos_transactions.csv data/

# 3. Start the API
docker compose up -d

# 4. Run the detection pipeline (~15–45 min depending on hardware)
pip install -r requirements-pipeline.txt
bash pipeline/run.sh "/path/to/CCTV Footage"

# 5. Verify
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```

## Live Dashboard

After ingesting events, open: **http://localhost:8000/dashboard/**

Shows live zone visit frequency, anomalies, and camera feed status — updates every 5 seconds.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Ingest detection events (batch ≤500, idempotent by event_id) |
| GET | `/stores/STORE_BLR_002/metrics` | Visitors, conversion rate, dwell per zone, queue depth |
| GET | `/stores/STORE_BLR_002/funnel` | Entry → Zone Visit → Billing Queue → Purchase |
| GET | `/stores/STORE_BLR_002/heatmap` | Zone visit frequency, normalised 0–100 |
| GET | `/stores/STORE_BLR_002/anomalies` | Queue spike, conversion drop, dead zone, stale feed |
| GET | `/health` | Per-camera feed status, STALE_FEED warnings |

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Expected: **19 passed**

## Test Coverage

```bash
pip install pytest-cov
pytest --cov=app --cov-report=term-missing tests/
```

Statement coverage: **77%**

## Detection Pipeline

```bash
# Install pipeline dependencies (runs locally on Mac, not in Docker)
pip install -r requirements-pipeline.txt

# Process all 5 cameras — downloads YOLOv8n (~6MB) on first run
# Processing time: ~15 min on Apple Silicon MPS, ~45 min on Intel CPU
python3 -m pipeline.detect \
    --footage "/path/to/CCTV Footage" \
    --layout  data/store_layout.json \
    --output  data/events.jsonl

# Feed events into the running API
./ingest.sh data/events.jsonl
```

> **Note:** The CCTV footage and data files are not included in this repository per challenge rules.

## Camera Mapping (confirmed from footage)

| File | Camera ID | Zone | Notes |
|------|-----------|------|-------|
| CAM 1.mp4 | CAM_SKINCARE | Skincare & Korean Beauty wall | ~15 min |
| CAM 2.mp4 | CAM_MAKEUP | Makeup brands floor + FOH | ~15 min |
| CAM 3.mp4 | CAM_ENTRY | Entry / Exit threshold | ~4 min, 12:00 IST |
| CAM 4.mp4 | CAM_BACKROOM | Backroom / Stockroom (staff only) | All detections = staff |
| CAM 5.mp4 | CAM_BILLING | Cash counter + Accessories | ~2 min, 20:11 IST |

## Architecture

```
CCTV Clips → YOLOv8n + ByteTrack → Zone Mapper → Staff Detector → emit.py
                                                                       ↓
                                                               events.jsonl
                                                                       ↓
                                                     ingest.sh → POST /events/ingest
                                                                       ↓
                                                     FastAPI + SQLite DB
                                                                       ↓
                                          /metrics /funnel /heatmap /anomalies /health
                                                                       ↓
                                                     /dashboard (live web UI)
```

## Store Details

- **Store ID:** STORE_BLR_002 (internal: ST1008)
- **Location:** Brigade Road, Bangalore
- **POS data:** April 10, 2026 — 24 orders, 12:15–21:40 IST
- **Footage:** April 10, 2026 — 5 cameras, 20:10–20:15 IST (floor) + 12:00–12:04 IST (entry)