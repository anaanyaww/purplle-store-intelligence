#!/bin/bash
# run.sh — One command to process all CCTV clips and feed events into the API
# Usage: bash pipeline/run.sh /path/to/CCTV\ Footage

set -e

FOOTAGE_DIR="${1:-$FOOTAGE_DIR}"
LAYOUT="data/store_layout.json"
OUTPUT="data/events.jsonl"
API_URL="${API_URL:-http://localhost:8000}"

if [ -z "$FOOTAGE_DIR" ]; then
    echo "Usage: bash pipeline/run.sh /path/to/CCTV\ Footage"
    echo "  or: FOOTAGE_DIR=/path/to/footage bash pipeline/run.sh"
    exit 1
fi

echo "=== Store Intelligence Pipeline ==="
echo "Footage : $FOOTAGE_DIR"
echo "Output  : $OUTPUT"
echo "API     : $API_URL"
echo ""

# Step 1: Run detection
echo "Step 1/2: Running detection pipeline..."
python3 -m pipeline.detect \
    --footage "$FOOTAGE_DIR" \
    --layout  "$LAYOUT" \
    --output  "$OUTPUT"

echo ""
echo "Step 2/2: Ingesting events into API..."
python3 pipeline/ingest_events.py "$OUTPUT" "$API_URL"

echo ""
echo "=== Done. Check your metrics: ==="
echo "  curl $API_URL/stores/STORE_BLR_002/metrics | python3 -m json.tool"
echo "  curl $API_URL/stores/STORE_BLR_002/funnel  | python3 -m json.tool"
