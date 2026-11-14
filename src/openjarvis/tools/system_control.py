"""System control tool -- open known applications and adjust system volume.

Windows-only (this deployment target). Deliberately NOT a general program
launcher: ``open_app`` only resolves names from a fixed alias map, the same
bounded-capability shape as file_read/file_write's allowed_dirs -- a known,
named action rather than an arbitrary command string (that risk is exactly
what shell_exec was removed for in this install, see security.allowed_dirs).
Volume control simulates the hardware media keys, which only ever move the
system volume up/down/mute -- there is no code-execution surface here.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

# Name (lowercased) -> target passed to os.startfile. A bare exe/command name
# relies on Windows' own PATH + "App Paths" registry resolution (the same
# lookup the Start menu "Run" box uses), so this works whether or not the app
# is actually on PATH, as long as Windows itself knows about it.
_APP_ALIASES: dict[str, str] = {
    "spotify": "spotify:",  # registered URI scheme -- launches the installed app
    "steam": "steam:",  # same -- Steam's installer registers this scheme too
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "code": "code",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "settings": "ms-settings:",
    "terminal": "wt.exe",
    "cmd": "cmd.exe",
    "task manager": "taskmgr.exe",
}

# Virtual-key codes for the hardware media keys (winuser.h).
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_KEYEVENTF_KEYUP = 0x0002


def _press_media_key(vk: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def _resolve_discord_update_exe() -> str | None:
    """Return the per-user Discord launcher path, or ``None`` if not found.

    Unlike Spotify/Steam, Discord's installer registers neither a URI scheme
    nor a System "App Paths" entry -- it installs per-user under
    ``%LOCALAPPDATA%\\Discord`` and is normally launched via its own
    ``Update.exe --processStart Discord.exe`` shim (what the Start Menu
    shortcut actually points at), so it needs its own resolver rather than a
    plain os.startfile(name) lookup.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    update_exe = os.path.join(local_app_data, "Discord", "Update.exe")
    return update_exe if os.path.isfile(update_exe) else None


@ToolRegistry.register("system_control")
class SystemControlTool(BaseTool):
    """Open known desktop applications and adjust system volume (Windows)."""

    tool_id = "system_control"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        app_list = ", ".join(sorted({"Spotify", "Discord", "Steam", "VS Code", "Chrome",
                                      "Edge", "Notepad", "Calculator", "File Explorer",
                                      "Word", "Excel", "PowerPoint", "Settings", "Terminal",
                                      "Task Manager"}))
        return ToolSpec(
            name="system_control",
            description=(
                "Open a known desktop application, or adjust the system volume "
                "(up, down, mute, unmute). Not for music -- use spotify_control "
                f"for that. Known apps: {app_list}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open_app", "volume"],
                        "description": "What to do.",
                    },
                    "app": {
                        "type": "string",
                        "description": "App name to open. Required for open_app.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "mute", "unmute"],
                        "description": "Volume direction. Required for volume.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "How many volume steps to apply (default 2, for up/down).",
                    },
                },
                "required": ["action"],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        if sys.platform != "win32":
            return ToolResult(
                tool_name="system_control",
                content="System control is only supported on Windows.",
                success=False,
            )
        action = params.get("action", "")
        if action == "open_app":
            return self._open_app(params.get("app", ""))
        if action == "volume":
            return self._volume(params.get("direction", ""), params.get("steps"))
        return ToolResult(
            tool_name="system_control",
            content=f"Unknown action: {action!r}",
            success=False,
        )

    def _open_app(self, app: str) -> ToolResult:
        if not app.strip():
            return ToolResult(tool_name="system_control", content="No app specified.", success=False)
        name = app.strip().lower()

        if name == "discord":
            update_exe = _resolve_discord_update_exe()
            if update_exe is None:
                return ToolResult(
                    tool_name="system_control",
                    content="Discord doesn't seem to be installed for this user.",
                    success=False,
                )
            try:
                import subprocess

                subprocess.Popen([update_exe, "--processStart", "Discord.exe"])
            except OSError as exc:
                return ToolResult(
                    tool_name="system_control",
                    content=f"Could not open Discord: {exc}",
                    success=False,
                )
            return ToolResult(tool_name="system_control", content="Opened Discord.", success=True)

        target = _APP_ALIASES.get(name)
        if target is None:
            known = ", ".join(sorted(set(_APP_ALIASES.keys()) | {"discord"}))
            return ToolResult(
                tool_name="system_control",
                content=(
                    f"'{app}' isn't in the list of apps I can open. Known names: {known}. "
                    "Ask the user if they want this app added."
                ),
                success=False,
            )
        try:
            os.startfile(target)  # noqa: S606 -- target is from the fixed alias map, not user input
        except OSError as exc:
            return ToolResult(
                tool_name="system_control",
                content=f"Could not open {app}: {exc}",
                success=False,
            )
        return ToolResult(tool_name="system_control", content=f"Opened {app}.", success=True)

    def _volume(self, direction: str, steps: Any) -> ToolResult:
        if direction not in ("up", "down", "mute", "unmute"):
            return ToolResult(
                tool_name="system_control",
                content="direction must be one of: up, down, mute, unmute.",
                success=False,
            )
        count = steps if isinstance(steps, int) and steps > 0 else 2
        count = min(count, 50)  # safety ceiling against a runaway loop
        try:
            if direction == "up":
                for _ in range(count):
                    _press_media_key(_VK_VOLUME_UP)
            elif direction == "down":
                for _ in range(count):
                    _press_media_key(_VK_VOLUME_DOWN)
            else:
                # mute and unmute both toggle the same key -- Windows has no
                # separate "unmute" key, so this is the best available action
                # for both; if already in the target state it's a no-op.
                _press_media_key(_VK_VOLUME_MUTE)
        except OSError as exc:
            return ToolResult(
                tool_name="system_control",
                content=f"Could not adjust volume: {exc}",
                success=False,
            )
        verb = {"up": "Volume increased.", "down": "Volume decreased.",
                "mute": "Toggled mute.", "unmute": "Toggled mute."}[direction]
        return ToolResult(tool_name="system_control", content=verb, success=True)


__all__ = ["SystemControlTool"]
