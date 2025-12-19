import sqlite3
import os

# Assuming sqlite for dev, or I can check connection string
# The config says DATABASE_URL.replace("+asyncpg", "")
# Let's try to connect to the DB and inspect columns.
# Since I don't know the exact DB type/creds easily without parsing config, 
# I'll use the app's engine.

from sqlmodel import create_engine, inspect
from app.core.config import settings

sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
engine = create_engine(sync_db_url)

inspector = inspect(engine)
columns = [c['name'] for c in inspector.get_columns('testrun')]

print(f"Columns in testrun: {columns}")

required = ['allowed_domains', 'domain_settings']
missing = [c for c in required if c not in columns]

if missing:
    print(f"MISSING COLUMNS: {missing}")
    # If missing, I might need to run a migration or alter table
    # For now, just report.
else:
    print("All required columns present.")
