import uuid
from fastapi import APIRouter
from loguru import logger

from app.utils.response import error_response, success_response
from app.model.chat_model import ChatRequest, ChatResponseData
from app.services.chat_service import process_chat_message

router = APIRouter()

@router.post("/", response_model=None)
async def chat(req: ChatRequest):
    req_id = str(uuid.uuid4())
    logger.info(f"[{req_id}] Received /chat POST request")

    try:
        # Delegate business logic to the service layer
        answer, data_dict = await process_chat_message(req_id, req.message)
        
        return success_response(ChatResponseData(
            request_id=req_id,
            answer=answer,
            data=data_dict
        ), 200)

    except Exception as e:
        logger.exception(f"[{req_id}] Error returning chat response: {e}")
        return error_response(str(e), 500)