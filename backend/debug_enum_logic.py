import os
from unittest.mock import MagicMock
import sys

# Mock settings before importing app modules
sys.modules['app.core.config'] = MagicMock()
sys.modules['app.core.config'].settings = MagicMock()
sys.modules['app.core.config'].settings.DATABASE_URL = "postgresql://mock"

from app.models import TestSuiteUpdate, ExecutionMode
from enum import Enum

def debug_enum_update():
    print("DEBUG: Testing Enum Update Logic")
    
    # Simulate the update payload
    update_payload = TestSuiteUpdate(execution_mode=ExecutionMode.SEPARATE)
    print(f"Payload: {update_payload}")
    
    # Simulate model_dump
    update_data = update_payload.model_dump(exclude_unset=True)
    print(f"Dumped Data: {update_data}")
    print(f"Type of execution_mode: {type(update_data['execution_mode'])}")
    
    # Simulate the fix logic
    db_suite = MagicMock()
    
    for key, value in update_data.items():
        if isinstance(value, Enum):
            print(f"Converting Enum {value} to {value.value}")
            value = value.value
        setattr(db_suite, key, value)
        
    print(f"Resulting attribute on db_suite: {db_suite.execution_mode}")
    print(f"Type of attribute: {type(db_suite.execution_mode)}")

if __name__ == "__main__":
    debug_enum_update()
