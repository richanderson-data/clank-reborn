"""Tool registry and the default read-only toolset."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..errors import PermissionDenied, ToolError
from . import fs
from .permissions import Sandbox

ToolFunc = Callable[..., str]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    func: ToolFunc

    def wire_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    denied: bool = False


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec], autonomy=None):
        self._tools = {tool.name: tool for tool in tools}
        self.autonomy = autonomy

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict]:
        return [tool.wire_schema() for tool in self._tools.values()]

    def describe(self) -> str:
        lines = []
        for tool in self._tools.values():
            params = tool.parameters.get("properties", {})
            lines.append(f"  {tool.name}({', '.join(params)}) - {tool.description}")
        return "\n".join(lines)

    def execute(self, name: str, arguments: dict) -> ToolResult:
        """Run a tool. Never raises; failures come back as ToolResult."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                False,
                f"ERROR: unknown tool {name!r}. Available tools: "
                + ", ".join(self._tools)
                + ". Reply to the user without using a tool.",
            )
        if not isinstance(arguments, dict):
            return ToolResult(
                False,
                f"ERROR: arguments for {name} must be a JSON object, "
                f"got {type(arguments).__name__}.",
            )
        allowed = set(tool.parameters.get("properties", {}))
        clean = {k: v for k, v in arguments.items() if k in allowed}
        try:
            output = tool.func(**clean)
            return ToolResult(True, output)
        except PermissionDenied as exc:
            return ToolResult(False, f"DENIED: {exc}", denied=True)
        except ToolError as exc:
            return ToolResult(False, f"ERROR: {exc}")
        except TypeError as exc:
            return ToolResult(False, f"ERROR: invalid arguments for {name}: {exc}")
        except OSError as exc:
            return ToolResult(False, f"ERROR: {name} failed ({type(exc).__name__}); check the requested path and permissions")
        except Exception as exc:
            return ToolResult(False, f"ERROR: {name} failed unexpectedly ({type(exc).__name__}); try corrected arguments or another tool")


def build_default_registry(sandbox: Sandbox, autonomy=None, web_enabled=False) -> ToolRegistry:
    tools = [
        ToolSpec(
            name="list_directory",
            description=(
                "List files and subdirectories inside an allowed project directory. "
                "Relative paths are resolved against the project root."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list. Defaults to the project root.",
                    }
                },
            },
            func=lambda path=".": fs.list_directory(sandbox, path),
        ),
        ToolSpec(
            name="read_file",
            description=(
                "Read a UTF-8 text file inside an allowed project directory. "
                "Binary files are rejected; large files are truncated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to read."},
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to return (default 20000, max 200000).",
                    },
                },
                "required": ["path"],
            },
            func=lambda path, max_bytes=fs.DEFAULT_MAX_READ_BYTES: fs.read_file(
                sandbox, path, max_bytes
            ),
        ),
        ToolSpec(
            name="search_text",
            description=(
                "Case-insensitive literal text search across files under a path. "
                "Skips binary files and heavy directories such as .git and node_modules."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Literal text to find."},
                    "path": {
                        "type": "string",
                        "description": "File or directory to search. Defaults to the project root.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum matches to return (default 50, max 200).",
                    },
                },
                "required": ["query"],
            },
            func=lambda query, path=".", max_results=fs.DEFAULT_MAX_RESULTS: fs.search_text(
                sandbox, query, path, max_results
            ),
        ),
    ]
    from .git import git_info

    tools.append(ToolSpec(
        "git_info", "Inspect Git status, working diff, staged diff, or recent log. Read-only; no commits or pushes.",
        {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "diff", "staged", "log"]}, "path": {"type": "string"}}},
        lambda action="status", path=".": git_info(sandbox, action, path),
    ))
    if web_enabled:
        from .web_tools import tool_specs as web_specs

        tools.extend(web_specs())
    if autonomy is not None:
        from .autonomous import tool_specs

        tools.extend(tool_specs(autonomy))
    return ToolRegistry(tools, autonomy=autonomy)
