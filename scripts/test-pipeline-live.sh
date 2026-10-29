#!/bin/bash
# Test the pipeline-v2 with a real YouTube video
# Usage: ./scripts/test-pipeline-live.sh <youtube_id> [label]

set -e

YOUTUBE_ID="${1:?Usage: $0 <youtube_id> [label]}"
LABEL="${2:-test}"
MONGODB_URI="mongodb://localhost:27017/video-insight-engine?directConnection=true"
SUMMARIZER_URL="http://localhost:8000"

echo "=== Testing pipeline with YouTube ID: $YOUTUBE_ID ($LABEL) ==="

# 1. Create a video summary entry in MongoDB
VIDEO_SUMMARY_ID=$(mongosh "$MONGODB_URI" --quiet --eval "
  const result = db.videoSummaries.insertOne({
    youtubeId: '$YOUTUBE_ID',
    status: 'pending',
    createdAt: new Date(),
    updatedAt: new Date(),
  });
  print(result.insertedId.toString());
")

echo "Created videoSummary: $VIDEO_SUMMARY_ID"

# 2. Stream from summarizer and save output
OUTPUT_FILE="/tmp/pipeline-test-${LABEL}-$(date +%s).jsonl"
echo "Streaming to: $OUTPUT_FILE"

curl -sN "$SUMMARIZER_URL/summarize/stream/$VIDEO_SUMMARY_ID" \
  -H "Accept: text/event-stream" 2>&1 | while IFS= read -r line; do
  if [[ "$line" == data:* ]]; then
    DATA="${line#data: }"
    if [[ "$DATA" == "[DONE]" ]]; then
      echo ">>> DONE"
      break
    fi
    # Parse event type
    EVENT=$(echo "$DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('event','?'))" 2>/dev/null || echo "parse_error")
    echo ">>> Event: $EVENT"
    echo "$DATA" >> "$OUTPUT_FILE"
  fi
done

echo ""
echo "=== Results saved to: $OUTPUT_FILE ==="
echo "=== Events received: ==="
if [ -f "$OUTPUT_FILE" ]; then
  python3 -c "
import json, sys
events = []
with open('$OUTPUT_FILE') as f:
    for line in f:
        try:
            d = json.loads(line.strip())
            events.append(d.get('event', '?'))
        except:
            pass
for e in events:
    print(f'  - {e}')
print(f'Total: {len(events)} events')
"
else
  echo "  No output file created"
fi
