"""File delete tool — remove files or empty directories, with path validation.

Mirrors file_write.py's sandboxing: restricted to ``security.allowed_dirs``,
and refuses sensitive files via the same policy check. Deletion is
irreversible, so unlike file_write there is no silent recursive directory
removal -- only a single file, or an empty directory, per call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._paths import resolve_allowed_dirs
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("file_delete")
class FileDeleteTool(BaseTool):
    """Delete a single file or empty directory within allowed directories."""

    tool_id = "file_delete"

    def __init__(
        self,
        allowed_dirs: Optional[List[str]] = None,
    ) -> None:
        # See FileReadTool: None tracks the live config so a folder granted
        # at runtime works without a server restart.
        self._allowed_dirs_override = allowed_dirs

    def _current_allowed_dirs(self) -> List[Path]:
        return [
            Path(d).expanduser().resolve()
            for d in resolve_allowed_dirs(self._allowed_dirs_override)
        ]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_delete",
            description=(
                "Delete a file or an empty directory. Irreversible -- confirm"
                " the path with the user before calling this for anything they"
                " did not explicitly ask to remove."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file or empty directory to delete.",
                    },
                },
                "required": ["path"],
            },
            category="filesystem",
            required_capabilities=["file:write"],
        )

    def _is_path_allowed(self, path: Path, allowed_dirs: List[Path]) -> bool:
        if not allowed_dirs:
            return True
        resolved = path.resolve()
        return any(
            resolved == d or resolved.is_relative_to(d) for d in allowed_dirs
        )

    def execute(self, **params: Any) -> ToolResult:
        raw_path = params.get("path", "")
        if not raw_path:
            return ToolResult(tool_name="file_delete", content="No path provided.", success=False)

        allowed_dirs = self._current_allowed_dirs()
        path = Path(raw_path)
        if not path.is_absolute() and allowed_dirs:
            path = allowed_dirs[0] / path

        from openjarvis.security.file_policy import is_sensitive_file

        if is_sensitive_file(path):
            return ToolResult(
                tool_name="file_delete",
                content=f"Access denied: {raw_path} is a sensitive file.",
                success=False,
            )

        if not self._is_path_allowed(path, allowed_dirs):
            return ToolResult(
                tool_name="file_delete",
                content=(
                    f"Access denied: {raw_path} is outside the folders I'm allowed "
                    "to use. Ask the user to grant access to that folder from "
                    "Settings > Folder Access, then try again."
                ),
                success=False,
            )

        # Never allow deleting an allowed_dirs root itself, by path or symlink.
        resolved = path.resolve()
        if any(resolved == d for d in allowed_dirs):
            return ToolResult(
                tool_name="file_delete",
                content="Refusing to delete an allowed root directory itself.",
                success=False,
            )

        if not path.exists() and not path.is_symlink():
            return ToolResult(
                tool_name="file_delete",
                content=f"Path does not exist: {raw_path}",
                success=False,
            )

        try:
            if path.is_dir() and not path.is_symlink():
                path.rmdir()  # only succeeds if empty -- no recursive delete
            else:
                path.unlink()
        except OSError as exc:
            return ToolResult(
                tool_name="file_delete",
                content=f"Delete error: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name="file_delete",
            content=f"Deleted {raw_path}",
            success=True,
            metadata={"path": str(resolved)},
        )


__all__ = ["FileDeleteTool"]
