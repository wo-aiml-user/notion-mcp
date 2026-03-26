from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    user_query: str

class ChatResponseData(BaseModel):
    answer: str
    data: Optional[dict] = None
