"""Spotify playback control tool — play/pause/skip/volume/search via the Web API.

Separate from ``connectors/spotify.py``, which only *reads* recently-played
history for the knowledge index. This tool *acts* on the user's active
Spotify Connect session (phone, desktop app, speaker, ...), so it needs the
playback-control scopes (``user-modify-playback-state`` etc.) added to the
Spotify OAuth provider alongside ``user-read-recently-played``.

Requirements the model should surface to the user on failure, not guess past:
- Spotify must be connected via Settings > Data Sources (OAuth).
- Playback control is a Spotify Premium feature; the Web API 403s for Free.
- A device must already be playing/open somewhere (phone, desktop app, web
  player) — the Web API controls an existing Spotify Connect session, it
  does not launch the app.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_API_BASE = "https://api.spotify.com/v1"
_TOKEN_PATH = DEFAULT_CONFIG_DIR / "connectors" / "spotify.json"

_NOT_CONNECTED_MSG = (
    "Spotify is not connected. Connect it from Settings > Data Sources, then try again."
)
_NO_DEVICE_MSG = (
    "No active Spotify device found. Open Spotify on a phone, computer, or "
    "speaker and start/resume playback there once, then try again."
)
_PREMIUM_MSG = "Spotify Premium is required to control playback (free accounts cannot)."


class _SpotifyControlError(RuntimeError):
    """A user-facing failure — auth, device, or Premium — not a bug."""


def _load_tokens() -> dict[str, Any]:
    try:
        raw = _TOKEN_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise _SpotifyControlError(_NOT_CONNECTED_MSG) from exc
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _SpotifyControlError(_NOT_CONNECTED_MSG) from exc
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise _SpotifyControlError(_NOT_CONNECTED_MSG)
    return tokens


def _request(
    method: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    _retried: bool = False,
) -> httpx.Response:
    token = _load_tokens()["access_token"]
    try:
        resp = httpx.request(
            method,
            f"{_API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json=json_body,
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise _SpotifyControlError(f"Spotify request failed: {exc}") from exc

    if resp.status_code == 401 and not _retried:
        from openjarvis.connectors.oauth import refresh_oauth_token

        new_token = refresh_oauth_token("spotify", str(_TOKEN_PATH))
        if new_token:
            return _request(
                method, endpoint, params=params, json_body=json_body, _retried=True
            )
        raise _SpotifyControlError(_NOT_CONNECTED_MSG)
    return resp


def _active_or_first_device_id() -> str | None:
    """Return the active device's id, or the first available device's id.

    The Web API's playback endpoints 404 with NO_ACTIVE_DEVICE when a device
    exists (e.g. the desktop app is open) but Spotify hasn't marked it
    "active" yet -- that only happens once something has played on it. Rather
    than surface that as a dead end, fall back to the first device Spotify
    can see so the user doesn't have to manually press play once first.
    """
    resp = _request("GET", "me/player/devices")
    if resp.status_code != 200:
        return None
    devices = resp.json().get("devices") or []
    if not devices:
        return None
    for device in devices:
        if device.get("is_active"):
            return device.get("id")
    return devices[0].get("id")


def _playback_request(
    method: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    """Like :func:`_request`, but retries once against a specific device on 404."""
    resp = _request(method, endpoint, params=params, json_body=json_body)
    if resp.status_code == 404:
        device_id = _active_or_first_device_id()
        if device_id:
            retry_params = dict(params or {})
            retry_params["device_id"] = device_id
            resp = _request(method, endpoint, params=retry_params, json_body=json_body)
    return resp


def _raise_for_playback_error(resp: httpx.Response) -> None:
    if resp.status_code in (200, 204):
        return
    if resp.status_code == 404:
        raise _SpotifyControlError(_NO_DEVICE_MSG)
    if resp.status_code == 403:
        raise _SpotifyControlError(_PREMIUM_MSG)
    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        pass
    raise _SpotifyControlError(
        f"Spotify returned HTTP {resp.status_code}" + (f": {detail}" if detail else "")
    )


@ToolRegistry.register("spotify_control")
class SpotifyControlTool(BaseTool):
    """Control playback on the user's active Spotify Connect device."""

    tool_id = "spotify_control"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="spotify_control",
            description=(
                "Control Spotify playback: play/resume, pause, skip to next or "
                "previous track, set volume, search for a track/artist/album "
                "and play it, or check what's currently playing. Requires "
                "Spotify Premium and an active device (phone, computer, or "
                "speaker) already signed into Spotify."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "play",
                            "pause",
                            "next",
                            "previous",
                            "set_volume",
                            "search_and_play",
                            "now_playing",
                        ],
                        "description": "The playback action to perform.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Track, artist, or album to search for. Required "
                            "for search_and_play, e.g. 'Legiao Urbana Tempo "
                            "Perdido'."
                        ),
                    },
                    "volume_percent": {
                        "type": "integer",
                        "description": "Volume level 0-100. Required for set_volume.",
                    },
                },
                "required": ["action"],
            },
            category="media",
            metadata={"requires_connector": "spotify"},
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "")
        try:
            if action == "play":
                return self._play()
            if action == "pause":
                return self._pause()
            if action == "next":
                return self._skip("next")
            if action == "previous":
                return self._skip("previous")
            if action == "set_volume":
                return self._set_volume(params.get("volume_percent"))
            if action == "search_and_play":
                return self._search_and_play(params.get("query", ""))
            if action == "now_playing":
                return self._now_playing()
            return ToolResult(
                tool_name="spotify_control",
                content=f"Unknown action: {action!r}",
                success=False,
            )
        except _SpotifyControlError as exc:
            return ToolResult(tool_name="spotify_control", content=str(exc), success=False)

    def _play(self) -> ToolResult:
        resp = _playback_request("PUT", "me/player/play")
        _raise_for_playback_error(resp)
        return ToolResult(tool_name="spotify_control", content="Playback resumed.", success=True)

    def _pause(self) -> ToolResult:
        resp = _playback_request("PUT", "me/player/pause")
        _raise_for_playback_error(resp)
        return ToolResult(tool_name="spotify_control", content="Playback paused.", success=True)

    def _skip(self, direction: str) -> ToolResult:
        resp = _playback_request("POST", f"me/player/{direction}")
        _raise_for_playback_error(resp)
        verb = "Skipped to next track." if direction == "next" else "Went back to previous track."
        return ToolResult(tool_name="spotify_control", content=verb, success=True)

    def _set_volume(self, volume_percent: Any) -> ToolResult:
        if not isinstance(volume_percent, int) or not (0 <= volume_percent <= 100):
            return ToolResult(
                tool_name="spotify_control",
                content="volume_percent must be an integer between 0 and 100.",
                success=False,
            )
        resp = _playback_request(
            "PUT", "me/player/volume", params={"volume_percent": volume_percent}
        )
        _raise_for_playback_error(resp)
        return ToolResult(
            tool_name="spotify_control",
            content=f"Volume set to {volume_percent}%.",
            success=True,
        )

    def _search_and_play(self, query: str) -> ToolResult:
        if not query.strip():
            return ToolResult(
                tool_name="spotify_control",
                content="No search query provided.",
                success=False,
            )
        # market=from_token restricts results to tracks actually available (and
        # playable) in the account's own region. Without it, search can return
        # tracks the catalog does not license there; the play command is still
        # accepted (204) but the client silently clears and loads nothing,
        # which looks identical to a tool bug from the outside (#spotify-ctrl).
        resp = _request(
            "GET",
            "search",
            params={"q": query, "type": "track", "limit": 5, "market": "from_token"},
        )
        if resp.status_code != 200:
            _raise_for_playback_error(resp)
        items = (resp.json().get("tracks") or {}).get("items") or []
        if not items:
            return ToolResult(
                tool_name="spotify_control",
                content=f"No track found for {query!r}.",
                success=False,
            )
        # is_playable is only present when a market was passed; prefer the
        # first result that is actually playable, falling back to the top
        # result so a missing/odd field never blocks playback outright.
        track = next((t for t in items if t.get("is_playable", True)), items[0])
        uri = track.get("uri", "")
        name = track.get("name", "Unknown")
        artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
        album_uri = (track.get("album") or {}).get("uri", "")
        # A bare {"uris": [track_uri]} body is unreliable on Spotify's current
        # Web API -- it 204s but several devices silently never load anything
        # (confirmed against this account on both desktop and mobile, and
        # matches widely reported community issues). Playing the track's own
        # album as context with an offset onto it is the documented
        # workaround and loads reliably.
        if album_uri:
            play_body: dict[str, Any] = {
                "context_uri": album_uri,
                "offset": {"uri": uri},
            }
        else:
            play_body = {"uris": [uri]}
        play_resp = _playback_request("PUT", "me/player/play", json_body=play_body)
        _raise_for_playback_error(play_resp)
        return ToolResult(
            tool_name="spotify_control",
            content=f"Now playing: {name} — {artists}",
            success=True,
            metadata={"track_name": name, "artists": artists, "uri": uri},
        )

    def _now_playing(self) -> ToolResult:
        resp = _request("GET", "me/player/currently-playing")
        if resp.status_code == 204 or not resp.content:
            return ToolResult(
                tool_name="spotify_control", content="Nothing is currently playing.", success=True
            )
        if resp.status_code != 200:
            _raise_for_playback_error(resp)
        payload = resp.json()
        item = payload.get("item") or {}
        name = item.get("name", "Unknown")
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
        is_playing = payload.get("is_playing", False)
        state = "Playing" if is_playing else "Paused"
        return ToolResult(
            tool_name="spotify_control",
            content=f"{state}: {name} — {artists}",
            success=True,
            metadata={"track_name": name, "artists": artists, "is_playing": is_playing},
        )


__all__ = ["SpotifyControlTool"]
