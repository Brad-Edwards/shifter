"""Closed text/local-tool translation between Messages and Responses APIs."""

import json
from collections.abc import AsyncIterable, AsyncIterator

from shared.model_access import ContractError
from shared.model_access.messages import JsonObject, strict_json

from .provider_usage import usage_from_message, vertex_events


def responses_request(payload: JsonObject, *, model: str, count_only: bool) -> JsonObject:
    """Preserve the complete prompt and local tools; never enable hosted tools."""
    if payload.get("top_k") is not None or payload.get("stop_sequences"):
        raise ContractError("messages.unsupported_request")
    inputs = _response_inputs(payload["messages"])
    result = {"model": model, "input": inputs}
    if payload.get("system") is not None:
        system = payload["system"]
        result["instructions"] = system if isinstance(system, str) else "\n".join(item["text"] for item in system)
    if payload.get("tools"):
        result["tools"] = [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
                "strict": False,
            }
            for tool in payload["tools"]
        ]
    choice = payload.get("tool_choice")
    if choice:
        if choice["type"] == "tool":
            if not choice.get("name"):
                raise ContractError("messages.unsupported_request")
            result["tool_choice"] = {"type": "function", "name": choice["name"]}
        else:
            result["tool_choice"] = {"any": "required", "auto": "auto", "none": "none"}[choice["type"]]
        result["parallel_tool_calls"] = not choice.get("disable_parallel_tool_use", False)
    if not count_only:
        if payload["max_tokens"] < 16:
            raise ContractError("messages.unsupported_request")
        result.update(
            store=False,
            stream=payload.get("stream", False),
            max_output_tokens=payload["max_tokens"],
            truncation="disabled",
        )
        for key in ("temperature", "top_p"):
            if payload.get(key) is not None:
                result[key] = payload[key]
    return result


def _response_inputs(messages: list[JsonObject]) -> list[JsonObject]:
    """Translate complete local-tool history without server-side state."""
    inputs = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            inputs.append({"role": message["role"], "content": content})
            continue
        for block in content:
            kind = block["type"]
            if kind == "text":
                inputs.append({"role": message["role"], "content": block["text"]})
            elif kind == "tool_use" and message["role"] == "assistant":
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": block["id"],
                        "name": block["name"],
                        "arguments": json.dumps(block["input"], separators=(",", ":")),
                    }
                )
            elif kind == "tool_result" and message["role"] == "user":
                output = block["content"]
                if not isinstance(output, str):
                    output = "\n".join(item["text"] for item in output)
                if block.get("is_error"):
                    output = "Tool execution failed:\n" + output
                inputs.append({"type": "function_call_output", "call_id": block["tool_use_id"], "output": output})
            else:
                raise ContractError("messages.unsupported_request")
    return inputs


def response_message(value: JsonObject, *, model: str) -> JsonObject:
    """Include reasoning in billed output; reject missing or failed usage."""
    value = _object(value)
    status = value.get("status")
    incomplete = _object(value.get("incomplete_details") or {})
    if status != "completed" and not (status == "incomplete" and incomplete.get("reason") == "max_output_tokens"):
        raise ContractError("provider.incomplete_response")
    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise ContractError("provider.incomplete_usage")
    # Responses input_tokens already includes cached input, and output_tokens
    # already includes reasoning. No discounted or hidden component is omitted.
    normalized_usage = {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}
    usage_from_message({"usage": normalized_usage})
    content = []
    output = value.get("output")
    if not isinstance(output, list) or len(output) > 256:
        raise ContractError("provider.invalid_response")
    for item in output:
        item = _object(item)
        if item.get("type") == "message":
            for part in _parts(item.get("content")):
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    content.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    content.append({"type": "text", "text": part["refusal"]})
                else:
                    raise ContractError("provider.invalid_response")
        elif item.get("type") == "function_call":
            if not isinstance(item.get("call_id"), str) or not isinstance(item.get("name"), str):
                raise ContractError("provider.invalid_response")
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                raise ContractError("provider.invalid_response")
            content.append(
                {
                    "type": "tool_use",
                    "id": item["call_id"],
                    "name": item["name"],
                    "input": strict_json(arguments.encode()),
                }
            )
        elif item.get("type") != "reasoning":
            raise ContractError("provider.unsupported_response")
    reason = (
        "max_tokens"
        if status == "incomplete"
        else ("tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn")
    )
    return {
        "id": value.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": reason,
        "stop_sequence": None,
        "usage": normalized_usage,
    }


async def responses_events(chunks: AsyncIterable[bytes], *, model: str) -> AsyncIterator[JsonObject]:
    """Translate incremental text/tools and require the final complete usage record."""
    blocks = {}
    closed = set()
    started = stopped = False
    ignored = {
        "response.in_progress",
        "response.output_text.done",
        "response.refusal.done",
        "response.function_call_arguments.done",
        "response.reasoning_summary_part.added",
        "response.reasoning_summary_part.done",
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
    }
    async for event in vertex_events(chunks):
        event = _object(event)
        kind = event.get("type")
        if stopped:
            raise ContractError("provider.invalid_stream")
        if kind == "response.created":
            if started:
                raise ContractError("provider.invalid_stream")
            started = True
            yield {
                "type": "message_start",
                "message": {
                    "id": _object(event.get("response")).get("id", ""),
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
        elif not started:
            raise ContractError("provider.invalid_stream")
        elif kind in {"response.output_item.added", "response.content_part.added"}:
            converted = _start_block(event, blocks)
            if converted is not None:
                yield converted
        elif kind in {"response.output_text.delta", "response.refusal.delta", "response.function_call_arguments.delta"}:
            yield _delta_block(event, blocks, closed)
        elif kind in {"response.content_part.done", "response.output_item.done"}:
            key = _block_key(event)
            if key in blocks.keys() - closed:
                closed.add(key)
                yield {"type": "content_block_stop", "index": blocks[key][0]}
        elif kind in {"response.completed", "response.incomplete"}:
            final = _finished_response(event, model=model, blocks=blocks, closed=closed)
            yield {
                "type": "message_delta",
                "delta": {"stop_reason": final["stop_reason"], "stop_sequence": None},
                "usage": final["usage"],
            }
            yield {"type": "message_stop"}
            stopped = True
        elif kind not in ignored:
            raise ContractError("provider.invalid_stream")
    if not stopped:
        raise ContractError("provider.incomplete_usage")


def _start_block(event: JsonObject, blocks: dict) -> JsonObject | None:
    """Validate and assign a bounded stream block index."""
    item = event.get("item", {}) if event["type"] == "response.output_item.added" else event.get("part", {})
    item = _object(item)
    if item.get("type") not in {"function_call", "output_text", "refusal"}:
        if item.get("type") in {"message", "reasoning"}:
            return None
        raise ContractError("provider.unsupported_response")
    key = _block_key(event)
    if key in blocks or len(blocks) >= 256:
        raise ContractError("provider.invalid_stream")
    index = len(blocks)
    blocks[key] = (index, "tool_use" if item["type"] == "function_call" else "text")
    if item["type"] == "function_call" and (
        not isinstance(item.get("call_id"), str) or not isinstance(item.get("name"), str)
    ):
        raise ContractError("provider.invalid_stream")
    block = (
        {"type": "text", "text": ""}
        if item["type"] in {"output_text", "refusal"}
        else {"type": "tool_use", "id": item.get("call_id"), "name": item.get("name"), "input": {}}
    )
    return {"type": "content_block_start", "index": index, "content_block": block}


def _finished_response(event, *, model, blocks, closed):
    """Only a fully closed stream may publish the final usage record."""
    if set(blocks) != closed:
        raise ContractError("provider.invalid_stream")
    return response_message(event.get("response", {}), model=model)


def _object(value) -> JsonObject:
    if not isinstance(value, dict):
        raise ContractError("provider.invalid_response")
    return value


def _parts(value) -> list[JsonObject]:
    if not isinstance(value, list) or len(value) > 256:
        raise ContractError("provider.invalid_response")
    return [_object(part) for part in value]


def _block_key(event) -> tuple[int, int]:
    output = event.get("output_index")
    content = event.get("content_index", -1)
    if type(output) is not int or not 0 <= output < 256 or type(content) is not int or not -1 <= content < 256:
        raise ContractError("provider.invalid_stream")
    return output, content


def _delta_block(event, blocks, closed):
    key = _block_key(event)
    if key not in blocks or key in closed or not isinstance(event.get("delta"), str):
        raise ContractError("provider.invalid_stream")
    expected = "tool_use" if event["type"] == "response.function_call_arguments.delta" else "text"
    if blocks[key][1] != expected:
        raise ContractError("provider.invalid_stream")
    delta = (
        {"type": "text_delta", "text": event["delta"]}
        if expected == "text"
        else {"type": "input_json_delta", "partial_json": event["delta"]}
    )
    return {"type": "content_block_delta", "index": blocks[key][0], "delta": delta}
