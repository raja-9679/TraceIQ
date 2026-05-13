"""
Celery tasks for cleaning up stuck test runs.
"""
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import TestRun, TestStatus
from sqlmodel import create_engine

# Use sync engine for Celery worker
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=False)

TIMEOUT_MINUTES = getattr(settings, 'STALE_RUN_INACTIVITY_MINUTES', 15)


@celery_app.task(name="app.tasks.cleanup_tasks.cleanup_stuck_tests")
def cleanup_stuck_tests():
    """
    Find and mark tests that have been in RUNNING status for too long.
    Runs periodically to detect stuck tests that never received completion webhooks.
    """
    timeout_threshold = datetime.utcnow() - timedelta(minutes=TIMEOUT_MINUTES)
    cleaned_count = 0
    
    try:
        with Session(sync_engine) as session:
            # Find tests in RUNNING status older than timeout threshold
            statement = select(TestRun).where(
                TestRun.status == TestStatus.RUNNING,
                TestRun.updated_at < timeout_threshold
            )
            
            stuck_runs = session.exec(statement).all()
            
            for run in stuck_runs:
                # Calculate how long it's been running
                running_duration = datetime.utcnow() - run.updated_at
                minutes_running = int(running_duration.total_seconds() / 60)
                
                # Mark as ERROR with timeout message
                run.status = TestStatus.ERROR
                run.error_message = f"Test execution timed out after {minutes_running} minutes - no completion webhook received"
                run.updated_at = datetime.utcnow()
                
                session.add(run)
                cleaned_count += 1
                
                print(f"[Cleanup] Marked run {run.id} as ERROR (timeout after {minutes_running} minutes)")
            
            if cleaned_count > 0:
                session.commit()
                print(f"[Cleanup] Cleaned up {cleaned_count} stuck test runs")
            
    except Exception as e:
        print(f"[Cleanup] Error cleaning up stuck tests: {e}")
    
    return cleaned_count
