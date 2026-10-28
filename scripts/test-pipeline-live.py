#!/usr/bin/env python3
"""Test pipeline-v2 with real YouTube videos.

Usage:
    python scripts/test-pipeline-live.py <youtube_id> [label]
    python scripts/test-pipeline-live.py --all  # Test all video types
"""

import sys
import json
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId

MONGODB_URI = "mongodb://localhost:27017/video-insight-engine?directConnection=true"
SUMMARIZER_URL = "http://localhost:8000"

# Test videos for each output type
TEST_VIDEOS = {
    "explanation": ("dQw4w9WgXcQ", "Rick Astley - Never Gonna Give You Up (short, fallback test)"),
    "recipe": ("bJUiWdM__Qw", "Gordon Ramsay Scrambled Eggs"),
    "code_walkthrough": ("rfscVS0vtbw", "Python Tutorial - Python Full Course for Beginners"),
    "study_kit": ("rWimU0RneKk", "Quantum Computing Explained"),
    "trip_planner": ("kCWaPPmxBN0", "Japan Travel Guide"),
    "workout": ("ml6cT4AZdqI", "30 Min Full Body Workout"),
    "verdict": ("YTHGAf0Bx9M", "iPhone 15 Pro Review - MKBHD"),
    "highlights": ("2up7su7CeMo", "Lex Fridman Podcast"),
    "music_guide": ("dQw4w9WgXcQ", "Rick Astley (music test)"),
    "project_guide": ("s7wmiS2mSXY", "DIY Desk Build"),
}


def create_video_entry(youtube_id: str) -> str:
    """Create a pending video summary entry in MongoDB."""
    client = MongoClient(MONGODB_URI)
    db = client["video-insight-engine"]
    result = db.videoSummaryCache.insert_one({
        "youtubeId": youtube_id,
        "status": "pending",
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    })
    client.close()
    return str(result.inserted_id)


def stream_and_collect(video_summary_id: str, timeout: int = 180) -> list[dict]:
    """Stream SSE events from summarizer and collect them."""
    url = f"{SUMMARIZER_URL}/summarize/stream/{video_summary_id}"
    events = []

    try:
        response = requests.get(url, stream=True, headers={"Accept": "text/event-stream"}, timeout=timeout)
        response.raise_for_status()

        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue

            data_str = line[6:]  # Remove "data: " prefix
            if data_str == "[DONE]":
                events.append({"event": "DONE"})
                break

            try:
                data = json.loads(data_str)
                events.append(data)
                event_type = data.get("event", "unknown")

                if event_type == "metadata":
                    print(f"  metadata: {data.get('title', '?')[:60]}")
                elif event_type == "intent_detected":
                    print(f"  intent: {data.get('outputType')} (confidence: {data.get('confidence')})")
                elif event_type == "extraction_progress":
                    print(f"  extracting: {data.get('section', '?')} ({data.get('percent', '?')}%)")
                elif event_type == "extraction_complete":
                    output_type = data.get("outputType", "?")
                    data_keys = list(data.get("data", {}).keys()) if isinstance(data.get("data"), dict) else []
                    print(f"  extraction_complete: type={output_type}, keys={data_keys}")
                elif event_type == "enrichment_complete":
                    enrich_keys = list(data.keys())
                    print(f"  enrichment: {enrich_keys}")
                elif event_type == "synthesis_complete":
                    tldr = data.get("tldr", "")[:80]
                    print(f"  synthesis: tldr={tldr}...")
                elif event_type == "done":
                    ms = data.get("processingTimeMs", 0)
                    print(f"  done: {ms}ms ({ms/1000:.1f}s)")
                elif event_type == "error":
                    print(f"  ERROR: {data.get('message', '?')}")
                elif event_type in ("cached", "transcript_ready", "phase"):
                    print(f"  {event_type}")
                else:
                    print(f"  {event_type}: {str(data)[:80]}")
            except json.JSONDecodeError:
                print(f"  [raw] {data_str[:100]}")
    except requests.exceptions.Timeout:
        print(f"  TIMEOUT after {timeout}s")
        events.append({"event": "TIMEOUT"})
    except Exception as e:
        print(f"  ERROR: {e}")
        events.append({"event": "ERROR", "message": str(e)})

    return events


def validate_events(events: list[dict], expected_type: str | None = None) -> dict:
    """Validate the event sequence and return a report."""
    event_types = [e.get("event", "?") for e in events]
    report = {
        "total_events": len(events),
        "event_sequence": event_types,
        "has_metadata": "metadata" in event_types,
        "has_intent": "intent_detected" in event_types,
        "has_extraction": "extraction_complete" in event_types,
        "has_synthesis": "synthesis_complete" in event_types,
        "has_done": "done" in event_types or "DONE" in event_types,
        "has_error": "error" in event_types,
        "errors": [e.get("message", "") for e in events if e.get("event") == "error"],
    }

    # Check intent matches expected type
    if expected_type:
        intent_events = [e for e in events if e.get("event") == "intent_detected"]
        if intent_events:
            detected = intent_events[0].get("outputType")
            report["detected_type"] = detected
            report["type_match"] = detected == expected_type

    # Check extraction data
    extraction_events = [e for e in events if e.get("event") == "extraction_complete"]
    if extraction_events:
        ext_data = extraction_events[0].get("data", {})
        report["extraction_keys"] = list(ext_data.keys()) if isinstance(ext_data, dict) else []
        report["extraction_non_empty"] = sum(1 for v in ext_data.values() if v) if isinstance(ext_data, dict) else 0

    # Processing time
    done_events = [e for e in events if e.get("event") == "done"]
    if done_events:
        report["processing_ms"] = done_events[0].get("processingTimeMs", 0)

    return report


def test_video(youtube_id: str, label: str, expected_type: str | None = None) -> dict:
    """Test a single video through the pipeline."""
    print(f"\n{'='*60}")
    print(f"Testing: {label}")
    print(f"YouTube ID: {youtube_id}")
    if expected_type:
        print(f"Expected type: {expected_type}")
    print(f"{'='*60}")

    video_summary_id = create_video_entry(youtube_id)
    print(f"Created entry: {video_summary_id}")

    start = time.time()
    events = stream_and_collect(video_summary_id)
    elapsed = time.time() - start

    report = validate_events(events, expected_type)
    report["elapsed_seconds"] = round(elapsed, 1)
    report["youtube_id"] = youtube_id
    report["label"] = label

    # Print summary
    print(f"\n--- Report ---")
    print(f"  Events: {report['total_events']}")
    print(f"  Elapsed: {report['elapsed_seconds']}s")
    if report.get("processing_ms"):
        print(f"  Pipeline time: {report['processing_ms']}ms")
    if report.get("detected_type"):
        match_str = "MATCH" if report.get("type_match") else "MISMATCH"
        print(f"  Detected type: {report['detected_type']} ({match_str})")
    if report.get("extraction_keys"):
        print(f"  Extraction keys: {report['extraction_keys']}")
    if report["has_error"]:
        print(f"  ERRORS: {report['errors']}")

    status = "PASS" if (report["has_done"] and not report["has_error"]) else "FAIL"
    print(f"  Status: {status}")

    return report


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/test-pipeline-live.py <youtube_id> [label]")
        print("  python scripts/test-pipeline-live.py --all")
        print("  python scripts/test-pipeline-live.py --quick  (3 videos)")
        sys.exit(1)

    if sys.argv[1] == "--all":
        results = {}
        for expected_type, (yt_id, label) in TEST_VIDEOS.items():
            results[expected_type] = test_video(yt_id, label, expected_type)

        # Final summary
        print(f"\n{'='*60}")
        print("FINAL SUMMARY")
        print(f"{'='*60}")
        for expected_type, report in results.items():
            status = "PASS" if (report["has_done"] and not report["has_error"]) else "FAIL"
            detected = report.get("detected_type", "?")
            match = "Y" if report.get("type_match") else "N"
            print(f"  {expected_type:20s} -> {detected:20s} match={match} {status} ({report['elapsed_seconds']}s)")

    elif sys.argv[1] == "--quick":
        # Test 3 diverse video types
        quick_tests = ["explanation", "recipe", "code_walkthrough"]
        for expected_type in quick_tests:
            yt_id, label = TEST_VIDEOS[expected_type]
            test_video(yt_id, label, expected_type)

    else:
        youtube_id = sys.argv[1]
        label = sys.argv[2] if len(sys.argv) > 2 else "manual"
        test_video(youtube_id, label)


if __name__ == "__main__":
    main()
