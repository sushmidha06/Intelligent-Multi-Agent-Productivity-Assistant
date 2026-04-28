"""Adapter that wraps MCP server tools as LangChain `StructuredTool`s,
so the Gemini function-calling agent can invoke them transparently.

Security: every tool's text output is passed through `sanitize_tool_output`
before being returned to the agent. This catches **indirect** prompt
injection — instructions embedded inside emails / PRs / GitHub issues that
the agent reads on the user's behalf. The check is non-destructive: we
prepend a security notice so the model knows to treat the content as data,
not as instructions."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError, create_model

from .guardrails import sanitize_tool_output
from .mcp_servers.base import McpServer
from .observability import metrics


log = logging.getLogger("sushmi.tools")


def _format_validation_error(tool_name: str, exc: ValidationError) -> str:
    """Convert Pydantic noise into a short, agent-readable hint so the LLM can
    retry the tool call with corrected args, instead of the raw stack trace
    leaking out to the user."""
    parts: list[str] = []
    for err in exc.errors():
        field = ".".join(str(x) for x in err.get("loc", ())) or "(arg)"
        etype = err.get("type", "")
        if etype == "missing":
            parts.append(f"`{field}` is required")
        elif "string_type" in etype:
            parts.append(f"`{field}` must be a string (e.g. a YYYY-MM-DD date)")
        elif "int_type" in etype or "int_parsing" in etype:
            parts.append(f"`{field}` must be an integer")
        elif "float_type" in etype or "float_parsing" in etype:
            parts.append(f"`{field}` must be a number")
        elif "bool_type" in etype:
            parts.append(f"`{field}` must be true/false")
        else:
            msg = err.get("msg", "invalid value")
            parts.append(f"`{field}`: {msg}")
    joined = "; ".join(parts) or "invalid arguments"
    return (
        f"Tool `{tool_name}` got invalid arguments: {joined}. "
        f"Please call the tool again with the correct values."
    )


def _schema_to_pydantic(name: str, schema: dict) -> type[BaseModel]:
    """Translate a (small subset of) JSON Schema into a Pydantic model
    so LangChain/Gemini can render the function signature."""
    fields: dict[str, tuple[Any, Any]] = {}
    props = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    type_map = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list}
    for key, spec in props.items():
        py_type = type_map.get(spec.get("type", "string"), str)
        default = ... if key in required else spec.get("default", None)
        fields[key] = (py_type, Field(default=default, description=spec.get("description", "")))
    if not fields:
        # Pydantic won't allow a totally empty model in some versions; add a dummy optional
        return create_model(f"{name}Args")
    return create_model(f"{name}Args", **fields)


def mcp_server_to_langchain_tools(server: McpServer, namespace: str | None = None) -> list[StructuredTool]:
    """Expose every tool on an MCP server as a LangChain StructuredTool."""
    ns = namespace or server.server_name
    out: list[StructuredTool] = []
    for tool in server.list_tools():
        tool_name = f"{ns}__{tool['name']}"
        args_model = _schema_to_pydantic(tool_name, tool.get("inputSchema") or {})

        def _make_callable(_server: McpServer, _tool_name: str, _full_name: str):
            def _call(**kwargs):
                # Strip None defaults to keep arg payload tight
                cleaned = {k: v for k, v in kwargs.items() if v is not None}
                result = _server.call_tool(_tool_name, cleaned)
                # LangChain tools must return strings (or serialisable); MCP
                # response is {content: [TextContent], isError}.
                texts = [c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text"]
                joined = "\n".join(texts) or "(empty)"
                if result.get("isError"):
                    return "ERROR: " + joined
                # Defence against indirect prompt injection — see module docstring.
                annotated, matched = sanitize_tool_output(joined)
                if matched:
                    metrics.incr("indirect_injection_detected_total", tool=_full_name)
                    log.warning(
                        "indirect_injection_in_tool_output",
                        extra={"tool": _full_name, "pattern": matched},
                    )
                return annotated
            return _call

        out.append(
            StructuredTool.from_function(
                name=tool_name,
                description=tool["description"],
                args_schema=args_model,
                func=_make_callable(server, tool["name"], tool_name),
            )
        )
    return out
