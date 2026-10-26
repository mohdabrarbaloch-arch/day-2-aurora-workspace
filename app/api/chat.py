"""Conversational endpoints: message history and streamed answers.

Answers stream over Server-Sent Events. SSE rather than WebSockets because the
traffic is strictly server-to-client, it survives proxies, and it needs no
connection bookkeeping on the server.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, OwnedWorkspace
from app.db import SessionLocal
from app.models import Asset, Message
from app.schemas import AskRequest, Citation, MessageOut
from app.services.providers import get_provider
from app.services.retrieval import Chunk, chunk_text

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["chat"])

# Streaming cadence. Small enough to feel live, large enough not to flood the
# connection with one-token frames.
_WORD_DELAY = 0.012


def _to_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        provider=message.provider,
        grounded=message.grounded,
        citations=[Citation(**c) for c in (message.citations_json or [])],
        latency_ms=message.latency_ms,
        created_at=message.created_at,
    )


def build_chunks(assets: list[Asset]) -> list[Chunk]:
    """Flatten every asset's extracted text into retrievable chunks.

    PDFs are chunked per page so a citation can name the page it came from;
    images and text files are chunked as a single document.
    """
    chunks: list[Chunk] = []
    for asset in assets:
        if not asset.has_text:
            continue
        if asset.kind == "pdf" and asset.page_count > 1:
            # extracted_text is stored as "[page N] ..." blocks — split on them
            # so page numbers survive into the citation.
            for block in asset.extracted_text.split("[page "):
                block = block.strip()
                if not block or "]" not in block:
                    continue
                number_part, _, body = block.partition("]")
                try:
                    page = int(number_part.strip())
                except ValueError:
                    page = None
                chunks.extend(
                    chunk_text(
                        body,
                        asset_id=asset.id,
                        filename=asset.filename,
                        page=page,
                    )
                )
        else:
            chunks.extend(
                chunk_text(
                    asset.extracted_text,
                    asset_id=asset.id,
                    filename=asset.filename,
                    page=1 if asset.kind == "image" else None,
                )
            )
    return chunks


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/messages", response_model=list[MessageOut], summary="Conversation history")
def list_messages(workspace: OwnedWorkspace) -> list[MessageOut]:
    ordered = sorted(workspace.messages, key=lambda m: m.created_at)
    return [_to_out(m) for m in ordered]


@router.post(
    "/messages",
    summary="Ask a question and stream the answer (SSE)",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        404: {"description": "Workspace not found"},
        422: {"description": "No readable content in this workspace"},
    },
)
async def ask(
    workspace: OwnedWorkspace,
    payload: AskRequest,
    db: DbSession,
) -> StreamingResponse:
    assets = db.scalars(select(Asset).where(Asset.workspace_id == workspace.id)).all()

    if not assets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add at least one image, PDF or text file before asking a question.",
        )
    if not any(a.has_text for a in assets):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "None of the files in this workspace contain readable text. "
                "For images, ensure the text is legible; for PDFs, the file may be a "
                "scan with no text layer."
            ),
        )

    # Persist the user turn before streaming so history survives a dropped
    # connection mid-answer.
    user_message = Message(workspace_id=workspace.id, role="user", content=payload.question)
    db.add(user_message)
    db.commit()

    workspace_id = workspace.id
    question = payload.question
    chunks = build_chunks(list(assets))

    async def event_stream() -> AsyncIterator[str]:
        provider = get_provider()
        try:
            result = await asyncio.to_thread(provider.answer, question, chunks)
        except Exception as exc:
            yield _sse("error", {"message": f"The provider failed: {exc}"})
            return

        yield _sse(
            "start",
            {"provider": result.provider, "grounded": result.grounded},
        )

        # Stream word by word so the UI paints progressively. The full text is
        # already computed, so this is presentation, not generation.
        words = result.content.split(" ")
        for position, word in enumerate(words):
            suffix = " " if position < len(words) - 1 else ""
            yield _sse("delta", {"text": word + suffix})
            await asyncio.sleep(_WORD_DELAY)

        citations = [
            {
                "asset_id": c.asset_id,
                "filename": c.filename,
                "page": c.page,
                "snippet": c.snippet,
                "score": c.score,
            }
            for c in result.citations
        ]

        # A fresh session: the request-scoped one is closed once the response
        # starts streaming.
        stored_id = ""
        session: Session | None = None
        try:
            session = SessionLocal()
            assistant = Message(
                workspace_id=workspace_id,
                role="assistant",
                content=result.content,
                provider=result.provider,
                grounded=result.grounded,
                citations_json=citations,
                latency_ms=result.latency_ms,
            )
            session.add(assistant)
            session.commit()
            session.refresh(assistant)
            stored_id = assistant.id
        except Exception:
            if session is not None:
                session.rollback()
        finally:
            if session is not None:
                session.close()

        yield _sse(
            "done",
            {
                "id": stored_id,
                "provider": result.provider,
                "grounded": result.grounded,
                "citations": citations,
                "latency_ms": result.latency_ms,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx from buffering the stream into one lump.
            "X-Accel-Buffering": "no",
        },
    )
