# Aurora

**Ask across your images and documents — and get an answer that shows you where it came from.**

Aurora is a multimodal workspace. Drop in a screenshot, a PDF and a text file, then ask one
question that spans all three. Every answer arrives with citation cards naming the exact file,
page and snippet it was built from. When your files don't cover the question, Aurora says so
instead of inventing something plausible.

That constraint is the whole design: the answer is **extracted from what you uploaded**, never
paraphrased from a model's memory. So "it can't make things up" is a property of the
architecture, not a promise in a README.

---

## What's inside

- **Multimodal ingestion** — JPEG, PNG, WebP, GIF, PDF, TXT and Markdown, all verified by
  magic bytes rather than by filename or declared content type.
- **Real OCR** — images are read with Tesseract, so text inside a screenshot becomes searchable
  alongside your documents. If the OCR engine isn't installed, image uploads still succeed and
  the file is simply marked as having no text.
- **PDF page-level citations** — text is extracted per page, so a citation can say
  `contract.pdf · p.7` instead of just naming the file.
- **A hand-written BM25 retrieval engine** — no vector database, no embeddings API, no key.
  Around 80 lines of fully unit-tested scoring code, which means the ranking is deterministic
  and can be asserted on in tests.
- **SSE streaming** — answers arrive word by word over Server-Sent Events, with citation cards
  attached in the final frame.
- **Auth that behaves** — JWT access tokens, bcrypt password hashing, and a login endpoint that
  returns an identical error for "no such account" and "wrong password" so it can't be used to
  enumerate accounts.
- **Ownership isolation** — every workspace query is scoped to its owner. Asking for someone
  else's workspace returns **404, not 403**, so an ID probe can't confirm the row exists.
- **EXIF stripping on upload** — images are re-encoded through Pillow, which drops GPS
  coordinates and camera metadata. What you upload is what gets stored, minus the location.
- **Honest about limits** — scanned PDFs with no text layer are flagged in the UI rather than
  silently returning nothing.

---

## Quick start

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-2-aurora-workspace.git
cd day-2-aurora-workspace

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then set SECRET_KEY
uvicorn app.main:app --reload
```

Open <http://localhost:8000>, create an account, and upload something.

`SECRET_KEY` is required in production. Generate one with:

```bash
openssl rand -hex 32
```

In development the app will start without it by generating an ephemeral key — but that means
sessions don't survive a restart, which is fine locally and unacceptable in production.

### With Docker (Postgres included)

```bash
export SECRET_KEY=$(openssl rand -hex 32)
docker compose up --build
```

The app comes up on port 8000 with Postgres 16 behind it, running as a non-root user with a
health check wired to `/api/health`.

### OCR

To make text inside images searchable, Tesseract needs to be present:

```bash
# Debian / Ubuntu
sudo apt-get install tesseract-ocr

# macOS
brew install tesseract
```

The Dockerfile already includes it. Without it, everything else works — images just contribute
a palette and dimensions instead of text.

---

## Configuration

Every variable is documented in [`.env.example`](.env.example). The ones that matter most:

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | — | **Required in production.** Signs JWTs. |
| `DATABASE_URL` | `sqlite:///./aurora.db` | Postgres works unchanged: `postgresql+psycopg://...` |
| `MAX_UPLOAD_MB` | `15` | Enforced while streaming, not from `Content-Length`. |
| `AI_PROVIDER` | `local` | `local` = built-in retrieval. `gemini` = rewrite via Gemini. |
| `GEMINI_API_KEY` | — | Only read when `AI_PROVIDER=gemini`. |
| `RETRIEVAL_TOP_K` | `3` | Chunks fed to the answer composer. |
| `RETRIEVAL_MIN_SCORE` | `0.35` | Below this, Aurora declines instead of guessing. |

The provider in use is reported by `/api/health` **and** returned with every answer, so the
interface can never imply a model answered when the retrieval engine did.

---

## API

Base path `/api`. Full interactive docs at `/api/docs` (OpenAPI JSON at `/api/openapi.json`).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + active provider + database status |
| `POST` | `/auth/register` | Create an account → `201` with a token |
| `POST` | `/auth/login` | Exchange credentials for a token |
| `GET` | `/auth/me` | Current account |
| `POST` | `/workspaces` | Create a workspace → `201` |
| `GET` | `/workspaces` | List your workspaces |
| `GET` | `/workspaces/{id}` | Workspace detail |
| `DELETE` | `/workspaces/{id}` | Delete workspace and its stored files → `204` |
| `POST` | `/workspaces/{id}/assets` | Upload an image, PDF or text file → `201` |
| `GET` | `/workspaces/{id}/assets` | List assets |
| `GET` | `/workspaces/{id}/assets/{aid}/file` | Download stored bytes |
| `DELETE` | `/workspaces/{id}/assets/{aid}` | Delete an asset → `204` |
| `GET` | `/workspaces/{id}/messages` | Conversation history |
| `POST` | `/workspaces/{id}/messages` | Ask a question → **SSE stream** |

Example — ask a question and stream the answer:

```bash
curl -N -X POST "$BASE/api/workspaces/$WS_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "what are the payment terms?"}'
```

The stream emits three event types:

```
event: start    → {"provider": "local", "grounded": true}
event: delta    → {"text": "Net "}
event: done     → {"citations": [...], "grounded": true, "latency_ms": 3.4}
```

See [`docs/api.md`](docs/api.md) for the full reference, status codes and error shapes.

---

## Testing and quality

```bash
pytest -q          # 54 tests
ruff check .       # clean
black --check .    # clean
```

54 tests, covering:

- **Retrieval** (13) — tokenisation, BM25 ranking, chunk overlap, sentence selection, and the
  decline-instead-of-guess behaviour.
- **Ingestion** (12) — magic-byte detection, EXIF stripping, downscaling, palette extraction,
  PDF parsing, and scanned-PDF detection.
- **Auth & API** (19) — bcrypt round-trips, token tampering, account enumeration, ownership
  isolation, upload limits, and SSE streaming end to end.
- **Deployment safety** (10) — the assertions described in the next section.

---

## A note on deployment

The previous project in this series deployed a FastAPI app that returned **HTTP 200 on `/` and
500 on every `/api/*` route.** The cause was a legacy `builds` block that bundled only
`api/index.py`, so `from app.main import app` failed at import time — and a catch-all rewrite
to `index.html` hid the crash behind a healthy-looking page.

Aurora is built so that failure can't hide. Three deliberate choices:

1. **`includeFiles: "{app/**,requirements.txt}"`** — the whole package ships with the
   entrypoint, so the import can't fail for want of `app/`.
2. **No static catch-all.** FastAPI serves the frontend itself, and only `/` returns HTML. An
   unknown path returns a JSON `404`.
3. **Failure is loud.** If the function fails to import, `/` returns **500** — a broken deploy
   you can see, rather than a working-looking shell you can't.

`tests/test_deployment_safety.py` asserts all three, so a future edit that reintroduces the
catch-all will fail CI rather than silently break production.

Deployment itself needs a Vercel token:

```bash
vercel --prod --yes --token "$VERCEL_TOKEN"
```

---

## Project layout

```
app/
  main.py            FastAPI app, static serving, explicit 404
  config.py          Settings with safe local defaults
  db.py              Engine, SQLite pragmas, session dependency
  models.py          SQLAlchemy 2.0 models (users, workspaces, assets, messages)
  schemas.py         Pydantic request/response models
  security.py        bcrypt + JWT (called directly, not via passlib)
  middleware.py      Rate limiter, security headers, CSP
  api/
    deps.py          Auth resolution, ownership scoping
    auth.py          register / login / me
    workspaces.py    workspaces + asset upload and download
    chat.py          history + SSE streaming
  services/
    retrieval.py     Chunking, BM25, sentence ranking, citations
    imaging.py       Validation, re-encode, OCR, palette
    documents.py     PDF and text extraction
    providers.py     Local (default) and Gemini adapters
  static/            index.html, styles.css, app.js (no build step)
api/index.py         Vercel entrypoint
tests/               54 tests
docs/                setup, usage, API reference
```

---

## Licence

MIT — see [LICENSE](LICENSE).

Built by [ABraz Baloch](https://github.com/mohdabrarbaloch-arch) — day 2 of a 30-day run of
production-ready full-stack projects.
