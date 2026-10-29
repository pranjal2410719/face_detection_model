"""
Central Trajectory Query Service & Inspector.
Queries the Central Event Store (SQLite & JSONL) to reconstruct chronological
trajectories of any student across all distributed camera nodes.
"""
import sys
import argparse
from pathlib import Path

import config
from central_server import CentralEventStore

def display_trajectory(student_id: str, date_filter: str = None, status_filter: str = None, 
                       storage_dir: Path = config.CENTRAL_STORAGE_DIR):
    store = CentralEventStore(storage_dir)
    sightings = store.query_trajectory(student_id, date_str=date_filter, status_filter=status_filter)

    if not sightings:
        print(f"\n[!] No sightings found for Student ID [{student_id}] in the Central Event Store.")
        if date_filter:
            print(f"    (Filtered by date: {date_filter})")
        return

    student_name = sightings[0].get("student_name") or student_id
    first_seen = sightings[0]["observed_at"]
    last_seen = sightings[-1]["observed_at"]
    last_location = sightings[-1]["camera_location"]
    last_camera = sightings[-1]["camera_id"]

    print("\n" + "=" * 90)
    print(" CENTRAL STUDENT TRAJECTORY REPORT (Multi-Camera)")
    print(f"  • Student ID     : {student_id}")
    print(f"  • Student Name   : {student_name}")
    print(f"  • Total Sightings: {len(sightings)}")
    print(f"  • First Observed : {first_seen.replace('T', ' ')[:19]}")
    print(f"  • Last Observed  : {last_seen.replace('T', ' ')[:19]}")
    print(f"  • Current Status : Seen at [{last_location}] ({last_camera})")
    print("=" * 90)
    print(f" {'#':<3} {'OBSERVED AT':<20} {'CAMERA ID':<14} {'LOCATION':<24} {'STATUS':<11} {'CONF'}")
    print("-" * 90)

    for idx, s in enumerate(sightings, 1):
        obs_clean = s["observed_at"].replace("T", " ")[:19]
        cam_id = s["camera_id"]
        loc = s["camera_location"]
        status = s["status"]
        conf = f"{float(s['confidence']) * 100:.1f}%"
        print(f" {idx:<3} {obs_clean:<20} {cam_id:<14} {loc:<24} {status:<11} {conf}")

    print("=" * 90 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Query student location trajectory across all cameras.")
    parser.add_argument("--id", required=True, help="Student ID to query (e.g., 2410719)")
    parser.add_argument("--date", default=None, help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--status", default=None, choices=["VERIFIED", "UNCERTAIN"], 
                        help="Filter by verification status (default: all)")
    parser.add_argument("--storage", default=str(config.CENTRAL_STORAGE_DIR), 
                        help="Central storage directory path")
    args = parser.parse_args()

    display_trajectory(args.id, args.date, args.status, Path(args.storage))

if __name__ == "__main__":
    main()
