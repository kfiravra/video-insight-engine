#!/bin/bash
# Integration Test Configuration
# Source this file in test scripts: source "$(dirname "$0")/lib/config.sh"

# ============================================
# Service URLs
# ============================================
API_URL="http://localhost:3000"
API_BASE="http://localhost:3000/api"
SUMMARIZER_URL="http://localhost:8000"
ASSISTANT_PORT="8001"
WEB_URL="http://localhost:5173"
MONGODB_URI="mongodb://localhost:27017/video-insight-engine?directConnection=true"

# ============================================
# Timeouts (seconds)
# ============================================
STARTUP_TIMEOUT=120          # Max wait for services to start
HEALTH_CHECK_TIMEOUT=30      # Max wait for health check
VIDEO_PROCESSING_TIMEOUT=180 # Max wait for video processing (3 min)
POLL_INTERVAL=3              # Seconds between status polls

# ============================================
# Test Credentials
# ============================================
TEST_EMAIL_PREFIX="integration-test"
TEST_PASSWORD='TestPass123!'
TEST_USER_NAME="Integration Test User"

# Generate unique test email
get_test_email() {
  echo "${TEST_EMAIL_PREFIX}-$(date +%s)-$$@example.com"
}

# ============================================
# Test Data
# ============================================

# Short video for testing (Rick Astley - Never Gonna Give You Up)
# Duration: ~3:30, well-known, stable
TEST_YOUTUBE_URL="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
TEST_YOUTUBE_ID="dQw4w9WgXcQ"

# Alternative short video for variety (1 minute sample)
ALT_YOUTUBE_URL="https://www.youtube.com/watch?v=jNQXAC9IVRw"
ALT_YOUTUBE_ID="jNQXAC9IVRw"

# ============================================
# Docker Compose
# ============================================
COMPOSE_FILE="docker-compose.yml"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# ============================================
# Cookie/Token Storage
# ============================================
COOKIE_JAR="/tmp/vie-integration-cookies.txt"
TOKEN_FILE="/tmp/vie-integration-token.txt"

# Store access token
store_token() {
  echo "$1" > "$TOKEN_FILE"
}

# Get stored access token
get_token() {
  cat "$TOKEN_FILE" 2>/dev/null || echo ""
}

# Clear stored credentials
clear_credentials() {
  rm -f "$COOKIE_JAR" "$TOKEN_FILE"
}

# ============================================
# Cleanup Patterns
# ============================================

# Email pattern for cleanup (matches test users)
CLEANUP_EMAIL_PATTERN="^integration-test-.*@example\\.com$"
