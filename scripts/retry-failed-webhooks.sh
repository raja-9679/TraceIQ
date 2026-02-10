#!/bin/bash
# Script to retry failed webhooks from the dead letter queue

echo "=== Retry Failed Webhooks ==="
echo ""

# Get count of failed messages
FAILED_COUNT=$(docker exec infrastructure-redis-1 redis-cli LLEN webhook:failed)
echo "Found $FAILED_COUNT failed webhooks"

if [ "$FAILED_COUNT" -eq 0 ]; then
    echo "No failed webhooks to retry"
    exit 0
fi

echo ""
echo "Moving failed webhooks back to main queue for retry..."

# Move all messages from failed queue back to results queue
MOVED=0
while [ $MOVED -lt $FAILED_COUNT ]; do
    # Pop from failed queue
    MESSAGE=$(docker exec infrastructure-redis-1 redis-cli RPOP webhook:failed)
    
    if [ -z "$MESSAGE" ]; then
        break
    fi
    
    # Extract runId for logging (basic parsing)
    RUN_ID=$(echo "$MESSAGE" | grep -o '"runId":[0-9]*' | grep -o '[0-9]*')
    
    # Remove error fields and push to main queue
    CLEAN_MESSAGE=$(echo "$MESSAGE" | jq 'del(.error, .failed_at)' 2>/dev/null || echo "$MESSAGE")
    
    echo "$CLEAN_MESSAGE" | docker exec -i infrastructure-redis-1 redis-cli -x LPUSH webhook:results > /dev/null
    
    MOVED=$((MOVED + 1))
    echo "  ✅ Moved run $RUN_ID back to queue"
done

echo ""
echo "✅ Moved $MOVED webhooks back to main queue"
echo "They will be processed within 10 seconds by the webhook processor"
echo ""
echo "Monitor progress:"
echo "  docker logs -f infrastructure-celery_worker-1 | grep Webhook"
