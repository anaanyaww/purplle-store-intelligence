#!/bin/bash
# ingest.sh — Feed detection output (events.jsonl) into the running API
# Usage: ./ingest.sh [events_file] [api_url]
set -e

EVENTS_FILE="${1:-data/events.jsonl}"
API_URL="${2:-http://localhost:8000}"

if [ ! -f "$EVENTS_FILE" ]; then
    echo "ERROR: Events file not found: $EVENTS_FILE"
    echo "Run the detection pipeline first: cd pipeline && bash run.sh"
    exit 1
fi

echo "Ingesting from $EVENTS_FILE → $API_URL"
python3 pipeline/ingest_events.py "$EVENTS_FILE" "$API_URL"
