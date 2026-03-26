import asyncio
import json
import time
from loguru import logger
from google import genai
from google.genai import types
from fastmcp import Client

from app.config import settings
from app.services.prompts import get_system_prompt

# ---------------------------------------------------------------------------
# Gemini client (direct SDK — no Langchain dependency)
# ---------------------------------------------------------------------------
gemini_client = genai.Client(api_key=settings.GOOGLE_API_KEY)

# ---------------------------------------------------------------------------
# FastMCP client — STDIO transport to @notionhq/notion-mcp-server
# ---------------------------------------------------------------------------
mcp_client = Client(settings.get_notion_mcp_config())


def _serialize_tool_result_for_model(value) -> str:
    """Convert tool results into text for Gemini function responses."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _get_tool_schema(tool) -> dict:
    """
    Return a raw JSON Schema dict suitable for Gemini `parameters_json_schema`.

    MCP tool schemas can legitimately contain `$ref`, `$defs`, `const`, and
    other JSON Schema features that the SDK rejects on the narrower
    `parameters` field.
    """
    raw_schema = getattr(tool, "inputSchema", None)
    if isinstance(raw_schema, dict) and raw_schema:
        return raw_schema
    return {"type": "object", "properties": {}}


def _build_tool_declarations(tools_list: list) -> list[dict]:
    """
    Convert FastMCP tool objects into Gemini function declaration dicts.

    MCP docs pass `tool.inputSchema` directly to the model provider. For the
    Gemini SDK, the matching field is `parameters_json_schema`, which accepts
    raw JSON Schema without forcing it through the stricter `Schema` model.
    """
    declarations = []
    for tool in (tools_list or []):
        declarations.append({
            "name": getattr(tool, "name", "unknown_tool"),
            "description": getattr(tool, "description", ""),
            "parameters_json_schema": _get_tool_schema(tool),
        })
    return declarations


async def process_chat_message(user_query: str) -> tuple[str, dict | None]:
    """
    Processes a user chat message via the Notion MCP server + Gemini SDK.

    Flow (mirrors the official MCP docs example, Gemini edition):
      1. Connect to Notion MCP server, list available tools.
      2. Build Gemini function declarations directly from MCP tool schemas.
      3. Send user query to Gemini with tool declarations attached.
      4. Loop: for every function_call in the response, execute via MCP,
         feed function_response back into conversation, re-call Gemini.
      5. Return final text answer.

    Returns:
        (final_answer: str, data_dict: dict | None)
    """
    t0 = time.time()
    logger.info(f"process_chat_message — query: {user_query!r}")

    async with mcp_client:
        # ------------------------------------------------------------------ #
        # Step 1: Discover available Notion tools
        # ------------------------------------------------------------------ #
        tools_list = await mcp_client.list_tools()
        tool_declarations = _build_tool_declarations(tools_list)
        logger.info(f"MCP tools available: {[d['name'] for d in tool_declarations]}")

        gemini_tools = [types.Tool(function_declarations=tool_declarations)]
        config = types.GenerateContentConfig(tools=gemini_tools)

        # ------------------------------------------------------------------ #
        # Step 2: Build initial conversation
        # ------------------------------------------------------------------ #
        system_prompt = get_system_prompt()
        contents: list = [
            types.Content(
                role="user",
                parts=[types.Part(text=f"{system_prompt}\n\n{user_query}")]
            )
        ]

        collected_tool_results: list[dict] = []

        # ------------------------------------------------------------------ #
        # Step 3–4: Agentic loop — keep calling until no more function_calls
        # ------------------------------------------------------------------ #
        while True:
            response = gemini_client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=config,
            )

            # Append the model's response turn to the conversation history
            contents.append(response.candidates[0].content)

            # Collect all function_call parts from this response
            function_calls = [
                part.function_call
                for part in response.candidates[0].content.parts
                if part.function_call is not None
            ]

            if not function_calls:
                # No more tool calls — model produced a final text answer
                break

            # Execute all tool calls (potentially in parallel)
            logger.info(f"Gemini requested {len(function_calls)} tool call(s): "
                        f"{[fc.name for fc in function_calls]}")

            async def execute_tool(fc) -> types.Part:
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}
                logger.info(
                    f"TOOL CALL | name={tool_name} | args={json.dumps(tool_args, default=str)}"
                )
                try:
                    result = await mcp_client.call_tool(tool_name, tool_args)
                    tool_result_payload = str(result)

                except Exception as exc:
                    logger.error(f"TOOL ERROR | name={tool_name} | error={exc}")
                    tool_result_payload = f"Tool execution failed: {exc}"

                tool_output = _serialize_tool_result_for_model(tool_result_payload)

                logger.info(
                    f"TOOL RESULT | name={tool_name} | result={tool_output}"
                )

                collected_tool_results.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": tool_result_payload,
                })

                return types.Part.from_function_response(
                    name=tool_name,
                    response={"result": tool_output},
                )

            # Run all tool calls concurrently
            function_response_parts = await asyncio.gather(
                *[execute_tool(fc) for fc in function_calls]
            )

            # Append all function responses in a single user turn
            contents.append(
                types.Content(role="user", parts=list(function_response_parts))
            )

        # ------------------------------------------------------------------ #
        # Step 5: Extract final text answer
        # ------------------------------------------------------------------ #
        final_answer = response.text or ""
        data_dict = {"tools_called": collected_tool_results} if collected_tool_results else None

        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(f"Completed in {elapsed_ms}ms — tools used: {len(collected_tool_results)}")

        return final_answer, data_dict
