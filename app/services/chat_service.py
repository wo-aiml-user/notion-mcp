import os
import json
import time
from loguru import logger
from langchain_google_genai import ChatGoogleGenerativeAI
from fastmcp import Client

from app.services.prompts import get_planner_prompt, get_answer_prompt

# Initialize LLM
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    temperature=0.4
)

# Initialize FastMCP Client setup for Notion MCP
# Explicitly use NOTION_API_TOKEN for authentication with Notion MCP server
mcp_config = {
    "mcpServers": {
        "notion": {
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env": {
                "NOTION_API_TOKEN": os.getenv("NOTION_API_TOKEN", "")
            }
        }
    }
}

mcp_client = Client(mcp_config)

async def process_chat_message(req_id: str, message: str) -> tuple[str, dict]:
    """
    Processes the user chat message by integrating with Notion MCP to fetch context, 
    and then synthesizing a final response.
    Returns: (final_answer_string, fetched_data_dict)
    """
    t0 = time.time()
    logger.info(f"[{req_id}] Entering process_chat_message service for: {message}")

    # Connect to Notion MCP server inside the request lifecycle
    async with mcp_client:
        tools_list = await mcp_client.list_tools()
        resources_list = await mcp_client.list_resources()
        
        tool_names = [t.name for t in tools_list] if tools_list else []
        resource_uris = [r.uri for r in resources_list] if resources_list else []

        logger.info(f"[{req_id}] Notion MCP tools: {tool_names}, resources: {resource_uris}")

        # Define planner instructions (dynamic parsing of available capabilities)
        tools_json = json.dumps([{"name": getattr(t, 'name', ''), "description": getattr(t, 'description', '')} for t in (tools_list or [])])
        resources_json = json.dumps([{"uri": getattr(r, 'uri', ''), "name": getattr(r, 'name', '')} for r in (resources_list or [])])

        planner_prompt = get_planner_prompt(tools_json, resources_json, message)
        
        # LLM Call #1: Intent Detection + Tool Selection
        decision_resp = llm.invoke(planner_prompt)
        decision = decision_resp.content.strip()
        logger.info(f"[{req_id}] Planner decision length: {len(decision)}")

        tool_name = None
        tool_args = {}
        resource_uri = None
        fetched_data = None

        parsed_decision = decision
        if parsed_decision.startswith("```"):
            lines = parsed_decision.split('\n')
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and (lines[-1].strip().startswith("```") or lines[-1].strip() == "```"):
                lines = lines[:-1]
            parsed_decision = '\n'.join(lines).strip()

        try:
            call_info = json.loads(parsed_decision)
            resource_uri = call_info.get("resource")
            tool_name = call_info.get("tool") or call_info.get("name")
            tool_args = call_info.get("args", {})
        except Exception:
            logger.info(f"[{req_id}] Planner decision is not valid JSON. Passing through as direct answer.")
            return decision, None

        # MCP Client execution (NOT an LLM call)
        if tool_name:
            logger.info(f"[{req_id}] Invoking MCP tool '{tool_name}' with args {json.dumps(tool_args)}")
            result = await mcp_client.call_tool(tool_name, tool_args)
            
            if isinstance(result, str):
                fetched_data = result
            elif isinstance(result, list):
                fetched_data = result[0].text if (result and hasattr(result[0], 'text')) else str(result)
            elif hasattr(result, "content"):
                fetched_data = result.content[0].text if result.content else str(result)
            else:
                fetched_data = str(result)

        elif resource_uri:
            logger.info(f"[{req_id}] Fetching MCP resource '{resource_uri}'")
            res = await mcp_client.read_resource(resource_uri)
            if isinstance(res, str):
                fetched_data = res
            elif isinstance(res, list):
                fetched_data = res[0].text if (res and hasattr(res[0], 'text')) else str(res)
            elif hasattr(res, "contents") and getattr(res, "contents"):
                fetched_data = res.contents[0].text
            else:
                fetched_data = str(res)

        context_block = fetched_data if fetched_data else "{}"

        # LLM Call #2: Result Synthesis
        answer_prompt = get_answer_prompt(message, context_block)
        final_resp = llm.invoke(answer_prompt)
        
        data_dict = {"raw": str(fetched_data)[:1000]} if fetched_data else None

        logger.info(f"[{req_id}] Completed request in {int((time.time() - t0) * 1000)}ms")
        
        return final_resp.content, data_dict
