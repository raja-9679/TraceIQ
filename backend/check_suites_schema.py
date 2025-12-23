import asyncio
from sqlmodel import create_engine, inspect, text
from app.core.config import settings

# Adjust connection string for sync engine
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
engine = create_engine(sync_db_url)

def check_schema():
    print("Checking 'testsuite' table schema...")
    inspector = inspect(engine)
    
    try:
        columns = [c['name'] for c in inspector.get_columns('testsuite')]
    except Exception as e:
        print(f"Error getting columns for 'testsuite': {e}")
        return

    print(f"Existing columns: {columns}")

    # Columns defined in TestSuiteBase and TestSuite models:
    # name, description, execution_mode, parent_id, settings, inherit_settings, created_at, id
    required_columns = [
        'name', 
        'description', 
        'execution_mode', 
        'parent_id', 
        'settings', 
        'inherit_settings', 
        'created_at', 
        'id'
    ]

    missing = [col for col in required_columns if col not in columns]
    
    if missing:
        print(f"\n[!] MISSING COLUMNS in 'testsuite': {missing}")
    else:
        print("\n[OK] 'testsuite' table schema looks correct.")

if __name__ == "__main__":
    check_schema()
