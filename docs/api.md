# API Reference

Base URL: `/api` · OpenAPI: `/api/openapi.json` · Interactive: `/api/docs`

All request and response bodies are JSON unless stated. Timestamps are ISO 8601 UTC.

## Authentication

Send `Authorization: Bearer <token>` on every request except `/health`, `/auth/register` and
`/auth/login`.

A missing or malformed header returns `401`. An expired or tampered token also returns `401`,
with `WWW-Authenticate: Bearer`.

---

## `GET /health`

No auth. Used by the container health check.

```json
{
  "status": "ok",
  "app": "Aurora",
  "version": "1.0.0",
  "provider": "local",
  "database": "ok"
}
```

`database` is `"unavailable"` if the ping fails; `provider` is `"local"` or `"gemini"`
and reflects what will actually answer — Gemini is only used when selected *and* keyed.

---

## `POST /auth/register`

```json
{"email": "you@example.com", "password": "at-least-8-chars", "display_name": "Your Name"}
```

`201` →

```json
{"access_token": "eyJ...", "token_type": "bearer", "expires_in": 43200, "user": {"id": "...", "email": "...", "display_name": "...", "created_at": "..."}}
```

| Status | Condition |
|---|---|
| `201` | Created |
| `409` | Email already registered |
| `422` | Invalid email, password under 8 chars, or over 72 bytes |

---

## `POST /auth/login`

Same body shape minus `display_name`.

`200` with a token. **`401` for both a wrong password and an unknown email**, with an identical
body — so the endpoint cannot be used to discover which accounts exist.

Rate limited per IP: 10 attempts per minute.

---

## `GET /auth/me`

`200` → the current user object.

---

## `POST /workspaces`

```json
{"title": "Q4 contracts"}
```

`201` → `{"id": "...", "title": "Q4 contracts", "created_at": "...", "updated_at": "...", "asset_count": 0, "message_count": 0}`

`422` if the title is blank or longer than 120 characters. Titles are whitespace-collapsed, so
`"  Q4   contracts "` and `"Q4 contracts"` are the same workspace — and collide with `409`.

---

## `GET /workspaces`

`200` → array of workspace objects, newest first.

---

## `GET /workspaces/{workspace_id}`

`200` → workspace object. **`404` if it does not exist *or* belongs to another user** — the two
cases are deliberately indistinguishable.

---

## `DELETE /workspaces/{workspace_id}`

`204`, no body. Removes the row, its messages, and every stored file for its assets.

---

## `POST /workspaces/{workspace_id}/assets`

`multipart/form-data` with a `file` part. One file per request.

`201` →

```json
{
  "id": "...",
  "kind": "pdf",
  "filename": "contract.pdf",
  "mime": "application/pdf",
  "size_bytes": 284913,
  "page_count": 12,
  "has_text": true,
  "meta": {"total_pages": 12, "pages_with_text": 12, "has_text_layer": true, "likely_scanned": false, "char_count": 24210},
  "created_at": "..."
}
```

`kind` is one of `image`, `pdf`, `text`.

| Status | Condition |
|---|---|
| `201` | Stored |
| `413` | File exceeds `MAX_UPLOAD_MB` (counted while streaming) |
| `415` | Bytes are not an allowed type — magic-byte check, not the filename |
| `422` | Empty file, unparseable content, or a password-protected PDF |

For images, `meta` carries `palette` (up to 5 hex colours), `original_width`/`stored_width`,
`mean_luminance`, `ocr_chars` and `low_contrast`. The stored bytes are re-encoded to PNG, so the
file you download is not byte-identical to the upload.

---

## `GET /workspaces/{workspace_id}/assets`

`200` → array of asset objects.

---

## `GET /workspaces/{workspace_id}/assets/{asset_id}/file`

Returns the stored bytes with the stored media type and
`Content-Disposition: inline; filename="..."`. `404` if it belongs to someone else, `410` if the
row survives but the file on disk does not.

---

## `DELETE /workspaces/{workspace_id}/assets/{asset_id}`

`204`. Deletes the row and the file on disk.

---

## `GET /workspaces/{workspace_id}/messages`

`200` →

```json
[{"id": "...", "role": "user", "content": "what are the payment terms?", "provider": "local", "grounded": false, "citations": [], "latency_ms": 0.0, "created_at": "..."},
 {"id": "...", "role": "assistant", "content": "Net 30 from invoice date.", "provider": "local", "grounded": true, "citations": [...], "latency_ms": 3.4, "created_at": "..."}]
```

---

## `POST /workspaces/{workspace_id}/messages`

```json
{"question": "what are the payment terms?"}
```

**Returns `text/event-stream`, not JSON.** The question is persisted immediately; the answer is
persisted when the stream completes, so a dropped connection still leaves your question in
history.

| Event | Data |
|---|---|
| `start` | `{"provider": "local", "grounded": true}` |
| `delta` | `{"text": "Net "}` — one word plus its trailing space |
| `done` | `{"id": "...", "provider": "local", "grounded": true, "citations": [...], "latency_ms": 3.4}` |
| `error` | `{"message": "..."}` then the stream closes |

A citation:

```json
{"asset_id": "...", "filename": "contract.pdf", "page": 7, "snippet": "Net 30 from invoice date.", "score": 4.12}
```

`page` is `null` for plain text and `1` for images.

`422` if the workspace has no assets, or if none of them yielded readable text — the message
names which case it is, because "no files yet" and "your PDF is a scan" need different fixes.

Rate limited per IP: 30 questions per minute.

---

## Errors

Every error uses one shape:

```json
{"detail": "Human-readable message."}
```

Validation errors (`422`) from Pydantic use FastAPI's standard `detail` array instead, naming
each field and the reason. An unhandled exception returns `500` with a generic message — the
traceback is logged, never returned.

| Status | Meaning |
|---|---|
| `400` | Malformed request |
| `401` | Missing, expired or invalid token |
| `404` | Not found, or not yours |
| `409` | Conflict (duplicate email, duplicate workspace title) |
| `410` | The row exists but its file is gone |
| `413` | Payload too large |
| `415` | Unsupported media type |
| `422` | Validation failed, or nothing to answer from |
| `429` | Rate limited — respect `Retry-After` |
| `500` | Unhandled server error |

Because ownership failures return `404` rather than `403`, an ID probe cannot tell "this belongs
to someone else" apart from "this does not exist". That is intentional.
