"""
Maps (camera_id, bounding_box_center) → zone_id.
Rules derived from confirmed camera placements and store_layout.json.
"""

# camera_id → single zone (cameras covering exactly one zone)
SINGLE_ZONE_CAMERAS = {
    "CAM_BACKROOM": "BACKROOM",
    "CAM_ENTRY":    "ENTRY_EXIT",
    "CAM_SKINCARE": "SKINCARE_WALL",
}


def get_zone(camera_id: str, cx: float, cy: float,
             frame_w: int, frame_h: int) -> str:
    """
    Returns zone_id for a detection centroid (cx, cy) in a given camera.

    CAM_MAKEUP: left 40% of frame = FOH_CENTRAL (PMU station area),
                right 60% = MAKEUP_FLOOR (brand wall).
    CAM_BILLING: right 55%+ = ACCESSORIES display,
                 rest = CASH_COUNTER (POS terminal).
    All others: single zone per camera.
    """
    if camera_id in SINGLE_ZONE_CAMERAS:
        return SINGLE_ZONE_CAMERAS[camera_id]

    rel_x = cx / frame_w if frame_w > 0 else 0.5
    rel_y = cy / frame_h if frame_h > 0 else 0.5

    if camera_id == "CAM_MAKEUP":
        return "FOH_CENTRAL" if rel_x < 0.40 else "MAKEUP_FLOOR"

    if camera_id == "CAM_BILLING":
        return "ACCESSORIES" if rel_x > 0.55 else "CASH_COUNTER"

    return "UNKNOWN"


def is_entry_direction_inbound(track_history: list) -> bool:
    """
    Determine if movement is inbound (entering store) or outbound.
    track_history: list of (cy,) tuples, oldest first.
    Entry camera is mounted inside looking at the glass door.
    Inbound = person moves toward larger y (deeper into store).
    """
    if len(track_history) < 4:
        return True  # default: assume entry
    first_cy = sum(p[0] for p in track_history[:3]) / 3
    last_cy  = sum(p[0] for p in track_history[-3:]) / 3
    return last_cy > first_cy   # moving down/deeper = inbound
