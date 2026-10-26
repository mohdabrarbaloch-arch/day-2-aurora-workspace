"""Workspace and asset endpoints: create, list, upload, list, delete."""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession, OwnedWorkspace
from app.config import settings
from app.models import Asset, Workspace
from app.schemas import AssetOut, WorkspaceCreate, WorkspaceOut
from app.services.documents import DocumentError, extract_pdf, extract_text
from app.services.imaging import UnsupportedFileError, detect_kind, process_image

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Read in 1 MB slices: a single .read() on a large upload would buffer the whole
# body in memory before the size check ever runs.
_READ_CHUNK = 1024 * 1024


def _workspace_out(workspace: Workspace, *, assets: int, messages: int) -> WorkspaceOut:
    return WorkspaceOut(
        id=workspace.id,
        title=workspace.title,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        asset_count=assets,
        message_count=messages,
    )


def _read_capped(upload: UploadFile) -> bytes:
    """Read the stream, refusing to exceed the configured ceiling."""
    limit = settings.max_upload_bytes

    # Content-Length is only a hint — a hostile client can lie about it — but it
    # lets us reject an obviously oversized upload before reading anything.
    declared = upload.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
        )

    buffer = bytearray()
    upload.file.seek(0)
    while True:
        slice_ = upload.file.read(_READ_CHUNK)
        if not slice_:
            break
        buffer.extend(slice_)
        if len(buffer) > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
            )
    if not buffer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file is empty."
        )
    return bytes(buffer)


def _storage_root() -> Path:
    root = Path(settings.storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post(
    "",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
def create_workspace(payload: WorkspaceCreate, user: CurrentUser, db: DbSession) -> WorkspaceOut:
    workspace = Workspace(owner_id=user.id, title=payload.title)
    db.add(workspace)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a workspace with that name.",
        ) from exc
    db.refresh(workspace)
    return _workspace_out(workspace, assets=0, messages=0)


@router.get("", response_model=list[WorkspaceOut], summary="List your workspaces")
def list_workspaces(user: CurrentUser, db: DbSession) -> list[WorkspaceOut]:
    rows = db.scalars(
        select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at.desc())
    ).all()
    return [_workspace_out(ws, assets=len(ws.assets), messages=len(ws.messages)) for ws in rows]


@router.get("/{workspace_id}", response_model=WorkspaceOut, summary="Workspace detail")
def get_workspace(workspace: OwnedWorkspace) -> WorkspaceOut:
    return _workspace_out(workspace, assets=len(workspace.assets), messages=len(workspace.messages))


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a workspace and its files",
)
def delete_workspace(workspace: OwnedWorkspace, db: DbSession) -> None:
    # Remove files before the rows: once the cascade deletes the assets there is
    # no record of which paths belong to this workspace, and the bytes leak.
    for asset in workspace.assets:
        with contextlib.suppress(OSError):
            Path(asset.storage_path).unlink(missing_ok=True)
    db.delete(workspace)
    db.commit()


@router.post(
    "/{workspace_id}/assets",
    response_model=AssetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image, PDF or text file",
)
def upload_asset(
    workspace: OwnedWorkspace,
    file: UploadFile,
    db: DbSession,
) -> AssetOut:
    data = _read_capped(file)
    digest = hashlib.sha256(data).hexdigest()

    try:
        kind = detect_kind(data)
    except UnsupportedFileError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc

    safe_name = Path(file.filename or "upload").name[:200] or "upload"
    asset_id = uuid.uuid4().hex
    root = _storage_root()
    target = root / f"{asset_id}.bin"

    text = ""
    page_count = 0
    meta: dict[str, object] = {}
    mime = "application/octet-stream"
    payload = data

    try:
        if kind == "image":
            result = process_image(data, max_edge=settings.max_image_edge)
            text = result.ocr_text
            page_count = 1
            meta = result.meta
            mime = result.mime
            payload = result.payload
        elif kind == "pdf":
            doc = extract_pdf(data)
            text = doc.text
            page_count = doc.page_count
            meta = doc.meta
            mime = "application/pdf"
        else:
            doc = extract_text(data)
            text = doc.text
            page_count = doc.page_count
            meta = doc.meta
            mime = "text/plain"
    except (UnsupportedFileError, DocumentError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    target.write_bytes(payload)

    asset = Asset(
        id=asset_id,
        workspace_id=workspace.id,
        kind=kind,
        filename=safe_name,
        mime=mime,
        size_bytes=len(data),
        sha256=digest,
        storage_path=str(target),
        page_count=page_count,
        extracted_text=text,
        meta_json=meta,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    return AssetOut(
        id=asset.id,
        kind=asset.kind,  # type: ignore[arg-type]
        filename=asset.filename,
        mime=asset.mime,
        size_bytes=asset.size_bytes,
        page_count=asset.page_count,
        has_text=asset.has_text,
        meta=asset.meta_json,
        created_at=asset.created_at,
    )


@router.get(
    "/{workspace_id}/assets",
    response_model=list[AssetOut],
    summary="List assets in a workspace",
)
def list_assets(workspace: OwnedWorkspace) -> list[AssetOut]:
    return [
        AssetOut(
            id=a.id,
            kind=a.kind,  # type: ignore[arg-type]
            filename=a.filename,
            mime=a.mime,
            size_bytes=a.size_bytes,
            page_count=a.page_count,
            has_text=a.has_text,
            meta=a.meta_json,
            created_at=a.created_at,
        )
        for a in workspace.assets
    ]


@router.get(
    "/{workspace_id}/assets/{asset_id}/file",
    summary="Download the stored bytes of an asset",
)
def download_asset(workspace: OwnedWorkspace, asset_id: str, db: DbSession) -> FileResponse:
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == workspace.id))
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    path = Path(asset.storage_path)
    # Re-check containment: storage_path comes from the database, but a bad row
    # must never be able to read outside the storage root.
    try:
        path.resolve().relative_to(_storage_root())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid asset path."
        ) from exc
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Stored file is no longer available."
        )
    return FileResponse(path, media_type=asset.mime, filename=asset.filename)


@router.delete(
    "/{workspace_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete an asset",
)
def delete_asset(workspace: OwnedWorkspace, asset_id: str, db: DbSession) -> None:
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == workspace.id))
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    with contextlib.suppress(OSError):
        Path(asset.storage_path).unlink(missing_ok=True)
    db.delete(asset)
    db.commit()


__all__ = ["router"]
