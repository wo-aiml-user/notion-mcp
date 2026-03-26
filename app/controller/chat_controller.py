from fastapi import APIRouter
from loguru import logger

from app.utils.response import error_response, success_response
from app.model.chat_model import ChatRequest, ChatResponseData
from app.services.chat_service import process_chat_message

router = APIRouter()

@router.post("/query", response_model=None)
async def chat(req: ChatRequest):
    try:
        answer, data_dict = await process_chat_message(req.user_query)

        return success_response(ChatResponseData(
            answer=answer,
            data=data_dict
        ), 200)

    except Exception as e:
        logger.exception(f"Error returning chat response: {e}")
        return error_response(str(e), 500)
