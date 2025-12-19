from sqlmodel import create_engine, text, Session
from app.core.config import settings

def migrate():
    sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
    engine = create_engine(sync_db_url)
    with Session(engine) as session:
        # Check if columns exist
        try:
            session.exec(text("ALTER TABLE testrun ADD COLUMN allowed_domains JSON"))
            session.commit()
            print("Added allowed_domains column")
        except Exception as e:
            session.rollback()
            print(f"allowed_domains column might already exist: {e}")

        try:
            session.exec(text("ALTER TABLE testrun ADD COLUMN domain_settings JSON"))
            session.commit()
            print("Added domain_settings column")
        except Exception as e:
            session.rollback()
            print(f"domain_settings column might already exist: {e}")

        try:
            session.exec(text("ALTER TABLE testrun ADD COLUMN execution_log JSON"))
            session.commit()
            print("Added execution_log column")
        except Exception as e:
            session.rollback()
            print(f"execution_log column might already exist: {e}")

if __name__ == "__main__":
    migrate()
