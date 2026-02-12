"""add_parallel_to_execution_mode

Revision ID: 5108734f9b51
Revises: 
Create Date: 2026-02-03 16:40:44.710606

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5108734f9b51'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add 'parallel' value to the executionmode enum.
    
    This enables parallel test execution mode where test cases within a suite
    run concurrently instead of sequentially.
    """
    # PostgreSQL doesn't support ALTER TYPE ... ADD VALUE inside a transaction
    # So we need to use op.execute with proper connection handling
    
    # Check if the value already exists to make this migration idempotent
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum 
                WHERE enumlabel = 'parallel' 
                AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'executionmode')
            ) THEN
                ALTER TYPE executionmode ADD VALUE 'parallel';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    """
    Remove 'parallel' value from the executionmode enum.
    
    WARNING: PostgreSQL does not support removing enum values directly.
    This downgrade will fail if any rows in the database use the 'parallel' value.
    
    To properly downgrade:
    1. Update all test_suite rows with execution_mode='parallel' to another value
    2. Then run this migration
    
    For a production database, consider creating a new enum type and migrating data.
    """
    # Note: PostgreSQL doesn't support removing enum values
    # This is a limitation of PostgreSQL enums
    # We can only provide a warning or manual instructions
    
    op.execute("""
        DO $$
        BEGIN
            -- Check if any rows are using 'parallel'
            IF EXISTS (
                SELECT 1 FROM test_suite WHERE execution_mode = 'parallel'
            ) THEN
                RAISE EXCEPTION 'Cannot remove parallel enum value: rows exist with execution_mode=parallel. Please update these rows first.';
            END IF;
            
            -- PostgreSQL doesn't support ALTER TYPE ... DROP VALUE
            -- Manual intervention required:
            -- 1. Create new enum without 'parallel'
            -- 2. Alter column to use new enum
            -- 3. Drop old enum
            RAISE WARNING 'PostgreSQL does not support removing enum values. Manual migration required.';
        END
        $$;
    """)

