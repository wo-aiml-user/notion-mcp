from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    message: str

class ChatResponseData(BaseModel):
    request_id: str
    answer: str
    data: Optional[dict] = None
