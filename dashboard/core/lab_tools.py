"""Versioned tool definitions shared with MCP and other agent frameworks.

Adding an external framework requires no scheduler changes: it consumes these
JSON schemas and calls the same API as the manual workbench.
"""
from dataclasses import dataclass
from typing import Any
from core.lab_models import RunRequest


@dataclass(frozen=True)
class ResearchTool:
    name: str
    description: str
    method: str
    path: str
    schema: dict[str, Any]
    read_only: bool = True


EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
RUN_ID = {"type": "object", "properties": {"run_id": {"type": "string", "pattern": "^run_[a-f0-9]{32}$"}},
          "required": ["run_id"], "additionalProperties": False}

TOOLS = {
    item.name: item for item in (
        ResearchTool("research_capabilities", "Inspect research limits and available model integrations.", "GET", "/capabilities", EMPTY),
        ResearchTool("research_templates", "Discover registered learning methods and their parameter schemas.", "GET", "/templates", EMPTY),
        ResearchTool("research_datasets", "List explicitly imported research datasets; excludes account holdings.", "GET", "/datasets", EMPTY),
        ResearchTool("research_runs", "List saved research including failed and cancelled experiments.", "GET", "/runs", EMPTY),
        ResearchTool("research_run", "Submit a bounded manual or autonomous research task. Never places trades.", "POST", "/runs", RunRequest.model_json_schema(), False),
        ResearchTool("research_status", "Read progress, events and saved research results.", "GET", "/runs/{run_id}", RUN_ID),
        ResearchTool("research_cancel", "Cancel research and stop further model calls/computation.", "POST", "/runs/{run_id}/cancel", RUN_ID, False),
    )
}


def tool_definitions():
    return [{"name": item.name, "description": item.description, "inputSchema": item.schema,
             "annotations": {"readOnlyHint": item.read_only, "destructiveHint": False,
                             "idempotentHint": item.read_only or item.name == "research_cancel",
                             "openWorldHint": not item.read_only}}
            for item in TOOLS.values()]


def tool_request(name, arguments):
    import re
    if name not in TOOLS:
        raise ValueError("Unknown research tool")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    tool = TOOLS[name]
    if name == "research_run":
        arguments = RunRequest.model_validate(arguments).model_dump(mode="json")
        return tool.method, tool.path, arguments
    if "{run_id}" in tool.path:
        if set(arguments) != {"run_id"} or not re.fullmatch(r"run_[a-f0-9]{32}", str(arguments["run_id"])):
            raise ValueError("A valid run_id is required")
        return tool.method, tool.path.format(run_id=arguments["run_id"]), None
    if arguments:
        raise ValueError("This tool takes no arguments")
    return tool.method, tool.path, None
