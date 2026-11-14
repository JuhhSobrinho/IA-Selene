"""File read tool — read file contents with path validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._paths import resolve_allowed_dirs
from openjarvis.tools._stubs import BaseTool, ToolSpec

# Maximum file size to read (1 MB)
_MAX_SIZE_BYTES = 1_048_576


@ToolRegistry.register("file_read")
class FileReadTool(BaseTool):
    """Read file contents with optional directory restrictions."""

    tool_id = "file_read"

    def __init__(
        self,
        allowed_dirs: Optional[List[str]] = None,
    ) -> None:
        # None (the normal construction path) means "track the live config" --
        # resolved fresh on every call via _current_allowed_dirs(), so a
        # folder added at runtime (Settings) takes effect immediately rather
        # than needing a server restart. An explicit list is still honored
        # as a fixed override (used by callers/tests that want a pinned set).
        self._allowed_dirs_override = allowed_dirs

    def _current_allowed_dirs(self) -> List[Path]:
        return [
            Path(d).expanduser().resolve()
            for d in resolve_allowed_dirs(self._allowed_dirs_override)
        ]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_read",
            description=("Read the contents of a file. Returns the text content."),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": ("Max lines to return (default: all)."),
                    },
                },
                "required": ["path"],
            },
            category="filesystem",
        )

    def _is_path_allowed(self, path: Path, allowed_dirs: List[Path]) -> bool:
        """Check if path is within allowed directories."""
        if not allowed_dirs:
            return True
        resolved = path.resolve()
        return any(
            resolved == d or resolved.is_relative_to(d) for d in allowed_dirs
        )

    def execute(self, **params: Any) -> ToolResult:
        file_path = params.get("path", "")
        if not file_path:
            return ToolResult(
                tool_name="file_read",
                content="No path provided.",
                success=False,
            )
        allowed_dirs = self._current_allowed_dirs()
        path = Path(file_path)
        if not path.is_absolute() and allowed_dirs:
            # A relative name means "inside the sandbox", not the process cwd.
            path = allowed_dirs[0] / path
        # Block sensitive files (secrets, credentials, keys)
        from openjarvis.security.file_policy import is_sensitive_file

        if is_sensitive_file(path):
            return ToolResult(
                tool_name="file_read",
                content=f"Access denied: {file_path} is a sensitive file.",
                success=False,
            )
        if not path.exists():
            return ToolResult(
                tool_name="file_read",
                content=f"File not found: {file_path}",
                success=False,
            )
        if not path.is_file():
            return ToolResult(
                tool_name="file_read",
                content=f"Not a file: {file_path}",
                success=False,
            )
        if not self._is_path_allowed(path, allowed_dirs):
            return ToolResult(
                tool_name="file_read",
                content=(
                    f"Access denied: {file_path} is outside the folders I'm allowed "
                    "to use. Ask the user to grant access to that folder from "
                    "Settings > Folder Access, then try again."
                ),
                success=False,
            )
        # Check size
        try:
            size = path.stat().st_size
        except OSError as exc:
            return ToolResult(
                tool_name="file_read",
                content=f"Cannot stat file: {exc}",
                success=False,
            )
        if size > _MAX_SIZE_BYTES:
            return ToolResult(
                tool_name="file_read",
                content=f"File too large: {size} bytes (max {_MAX_SIZE_BYTES}).",
                success=False,
            )
        try:
            from openjarvis._rust_bridge import get_rust_module

            _rust = get_rust_module()
            text = _rust.FileReadTool().execute(str(path))
        except ImportError:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult(
                tool_name="file_read",
                content=f"Read error: {exc}",
                success=False,
            )
        max_lines = params.get("max_lines")
        if max_lines is not None and max_lines > 0:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:max_lines])
        return ToolResult(
            tool_name="file_read",
            content=text,
            success=True,
            metadata={"path": str(path.resolve()), "size_bytes": size},
        )


__all__ = ["FileReadTool"]
