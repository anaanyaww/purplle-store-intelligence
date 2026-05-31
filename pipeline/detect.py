"""
Main detection pipeline.
Usage: python3 -m pipeline.detect --footage /path/to/CCTV\ Footage --output data/events.jsonl
Processes each CAM file → emits structured events to JSONL.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def get_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"   # Apple Silicon GPU
    except Exception:
        pass
    return "cpu"


def build_camera_map(layout_path: str) -> dict:
    """Returns {filename: camera_id} from store_layout.json."""
    with open(layout_path) as f:
        layout = json.load(f)
    mapping = layout.get("camera_file_mapping", {})
    cameras = {c["camera_id"]: c for c in layout.get("cameras", [])}
    return mapping, cameras, layout


def process_clip(video_path: Path, camera_id: str, cam_config: dict,
                 model, output_path: str, device: str) -> int:
    """Process one video clip → append events to output_path. Returns event count."""
    from .timestamp_extractor import extract_base_timestamp
    from .zone_mapper import get_zone
    from .staff_detector import StaffDetector
    from .emit import EventEmitter

    base_ts  = extract_base_timestamp(str(video_path), camera_id)
    emitter  = EventEmitter(output_path, camera_id, base_ts)
    staff_det = StaffDetector()

    logger.info(f"Processing {video_path.name} as {camera_id} from {base_ts.isoformat()}")

    try:
        results = model.track(
            source=str(video_path),
            stream=True,
            persist=True,
            classes=[0],       # person class only
            conf=0.35,
            iou=0.45,
            tracker="bytetrack.yaml",
            device=device,
            verbose=False,
            imgsz=640,
        )

        frame_num = 0
        event_count = 0
        PROCESS_EVERY = 5   # sample every 5th frame → ~3fps

        for result in results:
            frame_num += 1
            if frame_num % PROCESS_EVERY != 0:
                continue

            frame = result.orig_img
            fps   = result.speed.get("fps", 15) or 15

            detections = []
            if result.boxes is not None and result.boxes.id is not None:
                for box in result.boxes:
                    if box.id is None:
                        continue
                    track_id = int(box.id.item())
                    bbox     = box.xyxy[0].cpu().numpy()
                    conf     = float(box.conf.item())
                    detections.append((track_id, bbox, conf))

            before = event_count
            emitter.process_frame(
                detections=detections,
                frame=frame,
                frame_num=frame_num,
                fps=fps,
                zone_fn=get_zone,
                staff_detector=staff_det,
            )

            if frame_num % 150 == 0:
                logger.info(f"  {camera_id}: frame {frame_num}, "
                            f"{len(detections)} detections")

    except Exception as e:
        logger.error(f"Error processing {video_path.name}: {e}", exc_info=True)
    finally:
        emitter.close()

    logger.info(f"  {camera_id}: done ({frame_num} frames processed)")
    return frame_num


def main():
    ap = argparse.ArgumentParser(description="Store Intelligence detection pipeline")
    ap.add_argument("--footage",  required=True, help="Path to CCTV Footage directory")
    ap.add_argument("--layout",   default="data/store_layout.json")
    ap.add_argument("--output",   default="data/events.jsonl")
    ap.add_argument("--cameras",  nargs="*", help="Only process these camera IDs")
    args = ap.parse_args()

    footage_dir = Path(args.footage)
    if not footage_dir.exists():
        logger.error(f"Footage directory not found: {footage_dir}")
        sys.exit(1)

    mapping, cameras, layout = build_camera_map(args.layout)
    device = get_device()
    logger.info(f"Using device: {device}")

    # Load YOLOv8n (downloads automatically on first run ~6MB)
    try:
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")
        logger.info("YOLOv8n loaded")
    except ImportError:
        logger.error("ultralytics not installed. Run: pip install -r requirements-pipeline.txt")
        sys.exit(1)

    # Clear output file
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    open(args.output, "w").close()
    logger.info(f"Output: {args.output}")

    processed = 0
    for filename, camera_id in mapping.items():
        if filename.startswith("_"):   # skip comment keys
            continue
        if args.cameras and camera_id not in args.cameras:
            continue

        video_path = footage_dir / filename
        if not video_path.exists():
            logger.warning(f"Video not found: {video_path}, skipping")
            continue

        logger.info(f"\n{'='*50}")
        process_clip(video_path, camera_id, cameras.get(camera_id, {}),
                     model, args.output, device)
        processed += 1

    # Count events
    try:
        with open(args.output) as f:
            n_events = sum(1 for _ in f)
        logger.info(f"\nDone. Processed {processed} cameras → {n_events} events in {args.output}")
    except Exception:
        logger.info(f"\nDone. Processed {processed} cameras.")


if __name__ == "__main__":
    main()
