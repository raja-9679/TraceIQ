#!/bin/bash
# Quick verification script for Redis queue webhook system

echo "=== Redis Queue Webhook System Verification ==="
echo ""

echo "1. Checking Redis queue depth..."
QUEUE_DEPTH=$(docker exec infrastructure-redis-1 redis-cli LLEN webhook:results 2>/dev/null)
if [ $? -eq 0 ]; then
    echo "   ✅ webhook:results queue depth: $QUEUE_DEPTH"
else
    echo "   ❌ Failed to connect to Redis"
    exit 1
fi

FAILED_DEPTH=$(docker exec infrastructure-redis-1 redis-cli LLEN webhook:failed 2>/dev/null)
echo "   ✅ webhook:failed queue depth: $FAILED_DEPTH"

echo ""
echo "2. Checking if celery_beat is running..."
BEAT_RUNNING=$(docker ps --filter "name=celery_beat" --format "{{.Names}}" 2>/dev/null)
if [ -n "$BEAT_RUNNING" ]; then
    echo "   ✅ Celery Beat is running: $BEAT_RUNNING"
else
    echo "   ⚠️  Celery Beat not found - you may need to start it"
fi

echo ""
echo "3. Checking if celery_worker is running..."
WORKER_RUNNING=$(docker ps --filter "name=celery_worker" --format "{{.Names}}" 2>/dev/null)
if [ -n "$WORKER_RUNNING" ]; then
    echo "   ✅ Celery Worker is running: $WORKER_RUNNING"
else
    echo "   ❌ Celery Worker not found - required for processing"
    exit 1
fi

echo ""
echo "4. Checking execution-engine..."
ENGINE_RUNNING=$(docker ps --filter "name=execution-engine" --format "{{.Names}}" 2>/dev/null)
if [ -n "$ENGINE_RUNNING" ]; then
    echo "   ✅ Execution Engine is running: $ENGINE_RUNNING"
else
    echo "   ❌ Execution Engine not found"
    exit 1
fi

echo ""
echo "=== System Status: Ready ✅ ==="
echo ""
echo "Next steps:"
echo "  1. Run a test from the UI"
echo "  2. Monitor logs: docker logs -f infrastructure-celery_worker-1"
echo "  3. Check queue: docker exec infrastructure-redis-1 redis-cli LLEN webhook:results"
echo ""
