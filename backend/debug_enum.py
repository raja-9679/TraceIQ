from app.models import ExecutionMode
from app.core.config import settings
from sqlmodel import create_engine, select, Session
from app.models import TestSuite
from sqlalchemy import text

# Setup sync engine
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
engine = create_engine(sync_db_url)

def check():
    print(f"Enum members: {list(ExecutionMode)}")
    print(f"Enum values: {[e.value for e in ExecutionMode]}")

    try:
        print(f"Testing lookup: ExecutionMode('continuous') -> {ExecutionMode('continuous')}")
    except Exception as e:
        print(f"Error looking up 'continuous': {e}")

    try:
        print(f"Testing lookup: ExecutionMode('starting') -> {ExecutionMode('starting')}")
    except Exception as e:
        print(f"Error looking up 'starting': {e}") # Expected to fail

    # Check DB contents
    with Session(engine) as session:
        # Raw check
        rows = session.exec(text("SELECT execution_mode FROM testsuite")).all()
        print(f"DB Row values: {rows}")

        # Model check
        print("Attempting to load suites via SQLModel...")
        try:
            suites = session.exec(select(TestSuite)).all()
            for s in suites:
                print(f"Suite: {s.name}, Mode: {s.execution_mode} (Type: {type(s.execution_mode)})")
        except Exception as e:
            print(f"\nCRITICAL ERROR loading suites: {e}")

if __name__ == "__main__":
    check()
