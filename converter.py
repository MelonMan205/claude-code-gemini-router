"""
Anthropic ↔ Gemini API format conversion.
"""

import json
import uuid
import logging
from typing import Any, AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

FINISH_REASON_MAP = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "end_turn",
    "RECITATION": "end_turn",
    "LANGUAGE": "end_turn",
    "OTHER": "end_turn",
    "FINISH_REASON_UNSPECIFIED": "end_turn",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"


def _build_tool_id_map(messages: list) -> dict[str, str]:
    """Return {tool_use_id: tool_name} by scanning all assistant messages."""
    mapping: dict[str, str] = {}
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    mapping[block["id"]] = block["name"]
    return mapping


def _content_to_gemini_parts(content: Any, tool_id_map: dict[str, str]) -> list:
    """Convert an Anthropic content value to a list of Gemini parts."""
    if isinstance(content, str):
        return [{"text": content}] if content else []

    if not isinstance(content, list):
        return []

    parts: list = []
    for block in content:
        if not isinstance(block, dict):
            continue

        btype = block.get("type")

        if btype == "text":
            text = block.get("text", "")
            if text:
                parts.append({"text": text})

        elif btype == "thinking":
            thinking = block.get("thinking", "")
            if thinking:
                # Gemini represents thinking with a "thought" flag
                parts.append({"text": thinking, "thought": True})

        elif btype == "tool_use":
            parts.append({
                "functionCall": {
                    "name": block["name"],
                    "args": block.get("input", {}),
                }
            })

        elif btype == "tool_result":
            tool_use_id = block.get("tool_use_id", "")
            func_name = tool_id_map.get(tool_use_id, tool_use_id)

            result_content = block.get("content", "")
            if isinstance(result_content, list):
                result_text = "".join(
                    rb.get("text", "")
                    for rb in result_content
                    if isinstance(rb, dict) and rb.get("type") == "text"
                )
            else:
                result_text = str(result_content) if result_content else ""

            parts.append({
                "functionResponse": {
                    "name": func_name,
                    "response": {"result": result_text},
                }
            })

        elif btype == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                parts.append({
                    "inlineData": {
                        "mimeType": source.get("media_type", "image/jpeg"),
                        "data": source.get("data", ""),
                    }
                })
            elif source.get("type") == "url":
                parts.append({
                    "fileData": {
                        "mimeType": source.get("media_type", "image/jpeg"),
                        "fileUri": source.get("url", ""),
                    }
                })

        elif btype == "document":
            # Best-effort: extract text if present
            source = block.get("source", {})
            if source.get("type") == "text":
                text = source.get("data", "")
                if text:
                    parts.append({"text": text})

    return parts


# JSON Schema fields that Gemini's function declarations do not support
_UNSUPPORTED_SCHEMA_KEYS = {
    "$schema", "$defs", "$ref", "$id", "$comment",
    "additionalProperties", "unevaluatedProperties",
    "propertyNames", "patternProperties",
    "if", "then", "else", "not", "allOf", "anyOf", "oneOf",
    "default", "examples", "const",
    "contentEncoding", "contentMediaType",
    "exclusiveMinimum", "exclusiveMaximum",
    "dependencies", "dependentSchemas", "dependentRequired",
}


def _clean_schema(schema: Any) -> Any:
    """Recursively remove JSON Schema fields unsupported by Gemini."""
    if isinstance(schema, dict):
        return {
            k: _clean_schema(v)
            for k, v in schema.items()
            if k not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_clean_schema(item) for item in schema]
    return schema


def _tools_to_gemini(tools: list) -> list:
    """Convert Anthropic tool definitions to Gemini functionDeclarations."""
    decls = []
    for tool in tools:
        # Skip built-in Claude tools that have no Gemini equivalent
        if tool.get("type", "").startswith("computer_"):
            continue
        if tool.get("type", "").startswith("text_editor_"):
            continue
        if tool.get("type", "").startswith("bash_"):
            continue

        decl: dict = {
            "name": tool["name"],
            "description": tool.get("description", ""),
        }
        schema = tool.get("input_schema")
        if schema:
            decl["parameters"] = _clean_schema(schema)

        decls.append(decl)

    return [{"functionDeclarations": decls}] if decls else []


def _tool_choice_to_gemini(tool_choice: dict | None, tools: list) -> dict | None:
    """Convert Anthropic tool_choice to Gemini toolConfig."""
    if not tool_choice or not tools:
        return None

    choice_type = tool_choice.get("type", "auto")
    if choice_type == "auto":
        return {"functionCallingConfig": {"mode": "AUTO"}}
    elif choice_type == "any":
        return {"functionCallingConfig": {"mode": "ANY"}}
    elif choice_type == "tool":
        return {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [tool_choice.get("name", "")],
            }
        }
    elif choice_type == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}

    return None


# ---------------------------------------------------------------------------
# Anthropic → Gemini
# ---------------------------------------------------------------------------

def anthropic_to_gemini(request_data: dict) -> tuple[str, dict]:
    """
    Convert an Anthropic /v1/messages request body to a Gemini generateContent
    request body. Returns (model_name, gemini_request_dict).
    """
    model: str = request_data.get("model", "gemini-2.5-pro")
    messages: list = request_data.get("messages", [])
    system = request_data.get("system", "")
    max_tokens: int = request_data.get("max_tokens", 8192)
    temperature = request_data.get("temperature")
    top_p = request_data.get("top_p")
    top_k = request_data.get("top_k")
    stop_sequences = request_data.get("stop_sequences")
    tools: list = request_data.get("tools", [])
    tool_choice = request_data.get("tool_choice")
    thinking = request_data.get("thinking", {})

    tool_id_map = _build_tool_id_map(messages)

    # --- Contents ---
    gemini_contents: list = []
    for msg in messages:
        role = msg.get("role", "user")
        gemini_role = "model" if role == "assistant" else "user"
        parts = _content_to_gemini_parts(msg.get("content", []), tool_id_map)

        if not parts:
            continue

        # Gemini requires strict user/model alternation; merge consecutive same-role turns
        if gemini_contents and gemini_contents[-1]["role"] == gemini_role:
            gemini_contents[-1]["parts"].extend(parts)
        else:
            gemini_contents.append({"role": gemini_role, "parts": parts})

    # --- Generation config ---
    generation_config: dict = {"maxOutputTokens": max_tokens}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if top_p is not None:
        generation_config["topP"] = top_p
    if top_k is not None:
        generation_config["topK"] = top_k
    if stop_sequences:
        generation_config["stopSequences"] = stop_sequences

    # Extended thinking → Gemini thinkingConfig
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens", 8000)
        generation_config["thinkingConfig"] = {"thinkingBudget": budget}

    gemini_request: dict = {
        "contents": gemini_contents,
        "generationConfig": generation_config,
    }

    # --- System instruction ---
    if system:
        if isinstance(system, str):
            gemini_request["systemInstruction"] = {"parts": [{"text": system}]}
        elif isinstance(system, list):
            parts = [
                {"text": b["text"]}
                for b in system
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if parts:
                gemini_request["systemInstruction"] = {"parts": parts}

    # --- Tools ---
    if tools:
        gemini_tools = _tools_to_gemini(tools)
        if gemini_tools:
            gemini_request["tools"] = gemini_tools
            tool_config = _tool_choice_to_gemini(tool_choice, tools)
            if tool_config:
                gemini_request["toolConfig"] = tool_config

    return model, gemini_request


# ---------------------------------------------------------------------------
# Gemini → Anthropic (non-streaming)
# ---------------------------------------------------------------------------

def gemini_to_anthropic(gemini_response: dict, model: str, request_id: str) -> dict:
    """Convert a Gemini generateContent response to Anthropic message format."""
    candidates = gemini_response.get("candidates", [])
    usage = gemini_response.get("usageMetadata", {})

    content_blocks: list = []
    stop_reason = "end_turn"

    if candidates:
        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "STOP")
        stop_reason = FINISH_REASON_MAP.get(finish_reason, "end_turn")

        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            if part.get("thought"):
                content_blocks.append({
                    "type": "thinking",
                    "thinking": part.get("text", ""),
                })
            elif "text" in part:
                content_blocks.append({
                    "type": "text",
                    "text": part["text"],
                })
            elif "functionCall" in part:
                fc = part["functionCall"]
                content_blocks.append({
                    "type": "tool_use",
                    "id": _make_tool_id(),
                    "name": fc["name"],
                    "input": fc.get("args", {}),
                })
                if stop_reason != "max_tokens":
                    stop_reason = "tool_use"

    # Gemini occasionally returns no candidates (e.g., safety block)
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    return {
        "id": request_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("promptTokenCount", 0),
            "output_tokens": usage.get("candidatesTokenCount", 0),
        },
    }


# ---------------------------------------------------------------------------
# Gemini → Anthropic (streaming)
# ---------------------------------------------------------------------------

async def stream_gemini_to_anthropic(
    gemini_stream: httpx.Response,
    model: str,
    request_id: str,
) -> AsyncGenerator[str, None]:
    """
    Consume a Gemini SSE stream and yield Anthropic-format SSE events.
    """

    def sse(event_type: str, data: dict) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    yield sse("message_start", {
        "type": "message_start",
        "message": {
            "id": request_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 1},
        },
    })
    yield sse("ping", {"type": "ping"})

    input_tokens = 0
    output_tokens = 0
    stop_reason = "end_turn"

    # Block tracking
    block_index = 0
    text_open = False
    thinking_open = False
    had_any_block = False

    async for line in gemini_stream.aiter_lines():
        if not line.startswith("data: "):
            continue

        raw = line[6:].strip()
        if not raw or raw == "[DONE]":
            continue

        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Unparseable SSE chunk: %.120s", raw)
            continue

        # Track token usage from any chunk that carries it
        usage_meta = chunk.get("usageMetadata", {})
        if usage_meta:
            input_tokens = usage_meta.get("promptTokenCount", input_tokens)
            output_tokens = usage_meta.get("candidatesTokenCount", output_tokens)

        candidates = chunk.get("candidates", [])
        if not candidates:
            continue

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        if finish_reason:
            stop_reason = FINISH_REASON_MAP.get(finish_reason, "end_turn")

        parts = candidate.get("content", {}).get("parts", [])

        for part in parts:
            if part.get("thought"):
                # ---- Thinking delta ----
                if text_open:
                    yield sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
                    text_open = False
                    block_index += 1

                if not thinking_open:
                    yield sse("content_block_start", {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "thinking", "thinking": ""},
                    })
                    thinking_open = True
                    had_any_block = True

                thinking_text = part.get("text", "")
                if thinking_text:
                    yield sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "thinking_delta", "thinking": thinking_text},
                    })

            elif "text" in part:
                # ---- Text delta ----
                if thinking_open:
                    yield sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
                    thinking_open = False
                    block_index += 1

                if not text_open:
                    yield sse("content_block_start", {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "text", "text": ""},
                    })
                    text_open = True
                    had_any_block = True

                text = part.get("text", "")
                if text:
                    yield sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "text_delta", "text": text},
                    })

            elif "functionCall" in part:
                # ---- Tool-use block ----
                if text_open:
                    yield sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
                    text_open = False
                    block_index += 1
                if thinking_open:
                    yield sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
                    thinking_open = False
                    block_index += 1

                fc = part["functionCall"]
                tool_id = _make_tool_id()

                yield sse("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": fc["name"],
                        "input": {},
                    },
                })
                had_any_block = True

                input_json = json.dumps(fc.get("args", {}))
                yield sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "input_json_delta", "partial_json": input_json},
                })

                yield sse("content_block_stop", {"type": "content_block_stop", "index": block_index})
                block_index += 1

                if stop_reason != "max_tokens":
                    stop_reason = "tool_use"

    # Close any block still open
    if text_open or thinking_open:
        yield sse("content_block_stop", {"type": "content_block_stop", "index": block_index})

    # Guarantee at least one content block so clients don't choke
    if not had_any_block:
        yield sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})

    yield sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })
    yield sse("message_stop", {"type": "message_stop"})
