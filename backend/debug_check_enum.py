import os
from unittest.mock import MagicMock
import sys

# Mock settings
sys.modules['app.core.config'] = MagicMock()
sys.modules['app.core.config'].settings = MagicMock()
# Use a default URL or try to read from .env if possible, but for now let's assume localhost
# Wait, I need the REAL connection to check the DB.
# I can't mock the connection string if I want to connect to the real DB.
# I need to find the real connection string.
# It's likely in .env or I can guess it.
# The user said "localhost:8000", so the DB is likely local.
# Typical: postgresql://postgres:password@localhost:5432/traceiq or similar.
# I'll try to read .env file directly.

def get_db_url():
    try:
        with open('.env', 'r') as f:
            for line in f:
                if line.startswith('DATABASE_URL='):
                    return line.split('=', 1)[1].strip()
    except:
        pass
    return "postgresql://user:password@localhost/dbname" # Fallback

db_url = get_db_url()
print(f"Using DB URL: {db_url}")

sys.modules['app.core.config'].settings.DATABASE_URL = db_url

# Now import the check script
from check_enum import check_enum
import asyncio

if __name__ == "__main__":
    asyncio.run(check_enum())
