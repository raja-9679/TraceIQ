from sqlmodel import Session, create_engine, select, desc
from app.models import TestRun
from app.core.config import settings

# Use sync engine
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
engine = create_engine(sync_db_url)

def check_latest_run():
    with Session(engine) as session:
        # Fetch a suite ID
        from app.models import TestSuite
        suite = session.exec(select(TestSuite).limit(1)).first()
        if suite:
            print(f"Suite ID: {suite.id}")
        else:
            print("No suites found.")

if __name__ == "__main__":
    check_latest_run()
