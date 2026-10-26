# Aurora — Architecture

A multimodal workspace where you drop in **images and documents**, then ask questions
across all of them at once. The answer cites exactly which asset and which page it came
from.

---

## 1. What problem this solves

Most "chat with your docs" tools have one fatal habit: they answer confidently from the
model's memory and never tell you where the sentence came from. Aurora inverts that.
**Every answer is produced from retrieved text, and every answer carries citations.**
If nothing in the uploaded material supports an answer, the app says so instead of
inventing one.

That constraint shapes the entire design — including the choice to ship a working
retrieval engine rather than a stub that pretends to call a model.

---

## 2. System diagram

```
                          ┌─────────────────────────────┐
                          │   Browser (mobile-first)    │
                          │   index.html + app.js       │
                          │   • upload tray             │
                          │   • asset grid (thumbs)     │
                          │   • chat + citation cards   │
                          └──────────────┬──────────────┘
                                         │ HTTPS
                       ┌─────────────────▼──────────────────┐
                       │        FastAPI application         │
                       │                                    │
                       │  /api/auth      register/login/me  │
                       │  /api/workspaces  CRUD             │
                       │  /api/workspaces/{id}/assets       │
                       │  /api/workspaces/{id}/messages     │
                       │        └── SSE text/event-stream   │
                       │  /api/health                       │
                       │                                    │
                       │  middleware:                      │
                       │   • CORS allowlist                │
                       │   • security headers (CSP, etc.)  │
                       │   • sliding-window rate limiter   │
                       └───────┬────────────┬─────────────┘
                           │            │             │
             ┌─────────────▼──┐  ┌──────▼───────┐  ┌──▼──────────────┐
             │  Ingestion     │  │  Retrieval   │  │  Providers     │
             │                │  │              │  │                │
             │ imaging.py     │  │ retrieval.py │  │ LocalRetrieval │
             │  • magic-byte  │  │  • chunker   │  │  (always on)   │
             │    sniffing    │  │  • tokenizer │  │                │
             │  • re-encode   │  │  • BM25      │  │ Gemini         │
             │  • EXIF strip  │  │  • sentence  │  │  (opt-in, only │
             │  • palette     │  │    ranking   │  │   with a key)  │
             │  • OCR         │  │  • citations │  │                │
             │                │  │              │  │ provider name  │
             │ documents.py   │  │              │  │ returned with  │
             │  • PDF text    │  │              │  │ every answer   │
             │  • TXT/MD      │  │              │  │                │
             └───────┬────────┘  └──────┬───────┘  └────────────────┘
                      │                 │
                      └────────┬────────┘
                     ┌─────────▼─────────┐
                     │  SQLAlchemy 2.0   │
                     │  SQLite  |  Postgres
                     │  users, workspaces,
                     │  assets, messages │
                     └───────────────────┘
```

---

## 3. Tech stack and *why*

| Layer | Choice | Reason |
|---|---|---|
| API | **FastAPI 0.115** | Async-native (needed for SSE streaming), Pydantic validation built in, OpenAPI for free |
| ORM | **SQLAlchemy 2.0** (typed `Mapped[]`) | Same code runs on SQLite for dev and Postgres in prod — no query rewriting |
| Validation | **Pydantic v2** | Request bodies rejected before they reach business logic |
| Auth | **JWT (HS256) + bcrypt** | bcrypt used directly, not through passlib — passlib's bcrypt backend has a version-detection failure on bcrypt ≥4 |
| Images | **Pillow** | Re-encode + EXIF strip (privacy) + palette extraction |
| OCR | **pytesseract** | Optional at runtime; degrades gracefully if the binary is absent |
| PDF | **pypdf** | Pure-Python, no system deps, works in serverless |
| Retrieval | **Hand-written BM25** | ~80 lines, zero dependencies, fully unit-testable, deterministic |
| Frontend | **Vanilla JS, no build step** | A 40 KB bundle that deploys anywhere beats a 400 KB framework for one screen |
| Deploy | **Vercel Python function** | Free tier, and the config is written to fail loudly (see §7) |

---

## 4. Data flow — upload to cited answer

**Ingest**
1. Client `POST`s multipart to `/api/workspaces/{id}/assets`. Size is capped by reading
   the stream in chunks, not by trusting `Content-Length`.
2. The file's **magic bytes** decide whether it is an image, a PDF, or text. The
   extension and the client's `Content-Type` are both treated as hints, never as truth.
3. Images are re-encoded to RGB (killing EXIF, GPS coordinates and ICC payloads), capped
   at 1600 px, then OCR'd and sampled for a dominant palette.
4. PDFs are parsed page by page; text is joined with page markers so citations can point
   at a page number.
5. Extracted text is chunked and stored on the asset row. A SHA-256 of the raw bytes is
   kept for integrity and duplicate detection.

**Ask**
1. `POST /api/workspaces/{id}/messages` stores the user turn, then opens an SSE stream.
2. The retriever tokenises the question, scores every chunk in the workspace with BM25,
   and keeps the top *k*.
3. Within those chunks it ranks individual sentences by query-term coverage and assembles
   an extractive answer — **no paraphrasing, so no hallucination**.
4. If the best score falls below the grounding threshold, the answer is an explicit
   "the uploaded material doesn't cover this" rather than a guess.
5. The stream emits `delta` events, then a final `done` event carrying the message id,
   the provider name, the citation array and the latency.

---

## 5. Data model

```
users ──< workspaces ──< assets
                   └──< messages
```

- `users(id, email UNIQUE, password_hash, display_name, created_at)`
- `workspaces(id, owner_id → users, title, created_at, updated_at)`
- `assets(id, workspace_id, kind, filename, mime, size_bytes, sha256, storage_path,
  page_count, extracted_text, meta_json, created_at)`
- `messages(id, workspace_id, role, content, provider, grounded, citations_json,
  latency_ms, created_at)`

Every workspace query is filtered by `owner_id` at the dependency layer, so an
ID-guessing attack returns **404, not 403** — it does not confirm the row exists.

---

## 6. Scaling notes

**Where this would hurt first:** retrieval is O(chunks) per question and runs in-process.
At ~10⁴ assets per workspace that becomes noticeable, and at ~10⁵ it is wrong.

| Pressure | Move |
|---|---|
| More chunks | Swap BM25 for **pgvector + HNSW**; keep BM25 as a lexical re-ranker (hybrid) |
| More users | Stateless API already — scale horizontally behind a load balancer; move rate-limit state to Redis |
| Large files | Stream uploads straight to S3/R2, parse in a worker queue, write status back to the asset row |
| Slow OCR | Push OCR to a background job; the asset row gets `status = processing` |
| Long answers | Already streamed; add resumable SSE with `Last-Event-ID` |
| Multi-region | Postgres read replicas; assets already content-addressed by SHA-256, so the CDN can key on it |

**What is deliberately *not* built:** background workers, S3 storage and Redis-backed
rate limiting. Each adds an operational dependency that would make a portfolio project
harder to run, and none is needed at single-user scale. The seams are marked in code so
they are an afternoon's work, not a rewrite.

---

## 7. Deployment notes — written to fail loudly

The previous project in this series deployed a FastAPI app that returned **HTTP 200 on
`/` and 500 on every `/api/*` route**. The cause was a Vercel build config that bundled
only the entrypoint file, so `from app.main import app` raised `ModuleNotFoundError` at
import time — and a catch-all rewrite `"/(.*)" → index.html` hid the crash behind a
friendly 200.

Aurora avoids both traps:

1. **The build ships the package.** `vercel.json` keeps a `builds` block but adds
   `"includeFiles": "{app/**,requirements.txt}"` for `api/index.py`, so the whole `app/`
   package travels with the entrypoint instead of being left behind.
2. **No static catch-all rewrite.** FastAPI mounts and serves the static directory
   itself, and only `/` returns HTML. An unknown path returns a JSON 404. The
   consequence is intentional: **if the function fails to import, `/` returns 500**, and
   the failure is visible instead of masked.

That is a deliberate trade — a broken deploy you can see beats a working-looking shell
you cannot.
