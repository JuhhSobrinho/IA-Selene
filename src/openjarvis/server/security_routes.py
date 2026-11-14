"""Folder-access management — lets the user grant/revoke directories the
file tools (file_read/file_write/file_delete) may touch, from Settings,
without restarting the server.

Mirrors the tomlkit read-modify-write-atomically pattern in
analytics_routes.py. The file tools themselves resolve ``security.allowed_dirs``
live on every call (see tools/_paths.py), so a change here takes effect on the
very next tool call -- no restart needed.
"""

from __future__ import annotations

import logging
import os
import string
import tempfile
import threading
from pathlib import Path
from typing import List, Optional

import tomlkit
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.core.config import load_config

logger = logging.getLogger(__name__)
_lock = threading.RLock()

router = APIRouter(prefix="/v1/security", tags=["security"])


class AllowedDirsResponse(BaseModel):
    allowed_dirs: List[str]


class AddFolderRequest(BaseModel):
    path: str
    create: bool = False


class RemoveFolderRequest(BaseModel):
    path: str


class BrowseEntry(BaseModel):
    name: str
    path: str


class BrowseResponse(BaseModel):
    path: str
    # "" means "show the drive list" (Windows) or the browse root; None means
    # there is nowhere higher to go.
    parent: Optional[str]
    folders: List[BrowseEntry]


def _write_allowed_dirs(dirs: List[str]) -> None:
    cfg = load_config()
    path = cfg._config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = (
        tomlkit.parse(path.read_text(encoding="utf-8"))
        if path.exists()
        else tomlkit.document()
    )
    if "security" not in doc:
        doc.add("security", tomlkit.table())
    arr = tomlkit.array()
    arr.extend(dirs)
    doc["security"]["allowed_dirs"] = arr
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(tomlkit.dumps(doc))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    load_config.cache_clear()


@router.get("/browse", response_model=BrowseResponse)
def browse_folders(path: str = "") -> BrowseResponse:
    """List subfolders of *path* for an in-app folder picker.

    No OS native picker is available from a plain browser tab -- the File
    System Access API's directory picker never exposes a real filesystem
    path (by design, for privacy), so a path-returning picker has to be
    implemented server-side instead. Called with an empty path, this lists
    Windows drive roots (there is no single filesystem root to start from).
    """
    raw = (path or "").strip()
    if not raw:
        if os.name == "nt":
            drives = [
                BrowseEntry(name=f"{letter}:\\", path=f"{letter}:\\")
                for letter in string.ascii_uppercase
                if os.path.exists(f"{letter}:\\")
            ]
            return BrowseResponse(path="", parent=None, folders=drives)
        raw = str(Path.home())

    target = Path(raw).expanduser()
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {raw}")
    resolved = target.resolve()

    at_drive_root = os.name == "nt" and resolved.parent == resolved
    if at_drive_root:
        parent: Optional[str] = ""  # back to the drive list
    elif resolved.parent == resolved:
        parent = None  # POSIX root -- nothing higher
    else:
        parent = str(resolved.parent)

    entries: List[BrowseEntry] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda c: c.name.lower())
    except (PermissionError, OSError):
        children = []
    for child in children:
        try:
            if child.is_dir() and not child.name.startswith("."):
                entries.append(BrowseEntry(name=child.name, path=str(child)))
        except OSError:
            continue  # unreadable child (permissions, broken junction, ...)

    return BrowseResponse(path=str(resolved), parent=parent, folders=entries)


@router.get("/allowed-dirs", response_model=AllowedDirsResponse)
def get_allowed_dirs(request: Request) -> AllowedDirsResponse:
    cfg = getattr(request.app.state, "config", None) or load_config()
    return AllowedDirsResponse(allowed_dirs=list(cfg.security.allowed_dirs))


@router.post("/allowed-dirs", response_model=AllowedDirsResponse)
def add_allowed_dir(body: AddFolderRequest) -> AllowedDirsResponse:
    raw = body.path.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Path is required.")
    folder = Path(raw).expanduser()
    if not folder.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute.")

    with _lock:
        if not folder.exists():
            if not body.create:
                raise HTTPException(
                    status_code=404,
                    detail=f"Folder does not exist: {folder}. Pass create=true to create it.",
                )
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"Could not create folder: {exc}"
                ) from exc
        elif not folder.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")

        resolved = str(folder.resolve())
        cfg = load_config()
        current = list(cfg.security.allowed_dirs)
        if resolved not in current:
            current.append(resolved)
            try:
                _write_allowed_dirs(current)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Could not save allowed_dirs: %s", exc)
                raise HTTPException(
                    status_code=500, detail="Could not save folder access."
                ) from exc
        return AllowedDirsResponse(allowed_dirs=current)


@router.post("/allowed-dirs/remove", response_model=AllowedDirsResponse)
def remove_allowed_dir(body: RemoveFolderRequest) -> AllowedDirsResponse:
    raw = body.path.strip()
    with _lock:
        cfg = load_config()
        current = [d for d in cfg.security.allowed_dirs if d != raw]
        if len(current) == len(cfg.security.allowed_dirs):
            raise HTTPException(status_code=404, detail="Folder not found in the allow-list.")
        try:
            _write_allowed_dirs(current)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not save allowed_dirs: %s", exc)
            raise HTTPException(
                status_code=500, detail="Could not save folder access."
            ) from exc
        return AllowedDirsResponse(allowed_dirs=current)


__all__ = ["router"]
