from pydantic import BaseModel
from typing import Optional
from app.models import TestStatus

class ForceCompleteRequest(BaseModel):
    status: Optional[TestStatus] = TestStatus.ERROR
    error_message: Optional[str] = "Manually marked as complete by administrator"
