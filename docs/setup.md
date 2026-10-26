# Setup

## Requirements

- Python 3.11 or newer
- 512 MB RAM (SQLite + in-process file storage)
- PostgreSQL 16 only if you want the Docker path
- Tesseract OCR, optional but recommended

## Local install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Environment

```bash
cp .env.example .env
```

Then set `SECRET_KEY`:

```bash
openssl rand -hex 32
```

In `APP_ENV=development` the app starts without one and generates an ephemeral key, logging a
warning. That is convenient locally and wrong in production — every restart invalidates all
sessions. In `APP_ENV=production` a missing key is a hard startup failure.

## Run

```bash
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. The API docs live at <http://localhost:8000/api/docs>.

The database file (`aurora.db`) and the upload directory (`storage/`) are created on first
start, so there is no migration step to forget.

## Docker

```bash
export SECRET_KEY=$(openssl rand -hex 32)
docker compose up --build
```

This brings up:

- `db` — Postgres 16, health-checked with `pg_isready`
- `app` — the API on port 8000, non-root user, health check on `/api/health`

Uploads persist in the `aurora_storage` volume and the database in `aurora_pgdata`.

## OCR

```bash
sudo apt-get install tesseract-ocr        # Debian / Ubuntu
brew install tesseract                    # macOS
```

Verify with `tesseract --version`.

OCR is optional: when the binary is missing, `pytesseract` raises at call time and the app
swallows it, storing an empty string as the image's text. Image uploads still succeed and the
thumbnail and palette still work — the image simply contributes nothing to search. There is no
startup error and no health field for it, so if image text is missing and everything else is
healthy, Tesseract is the first thing to check.

Keep in mind that Aurora does not OCR PDFs. A scanned PDF is flagged `has_text: false` rather
than run through OCR, because page-level OCR on a large scan is slow and better done offline.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SECRET_KEY is unset or still the default` at startup | `APP_ENV=production` with no key | Generate one with `openssl rand -hex 32`, or run in development |
| Uploads return `413` | File over `MAX_UPLOAD_MB` | Raise the limit, or send a smaller file |
| Uploads return `415` | Bytes don't match an allowed type | Check the magic bytes — renaming `.exe` to `.png` fails by design |
| Uploads return `422` | Parsing failed (empty file, corrupt PDF, password-protected PDF) | The response `detail` names the reason |
| Images upload but have no text | Tesseract missing, or the image has no legible text | Install Tesseract |
| `"database": "unavailable"` from `/api/health` | Bad `DATABASE_URL` | Check the DSN and that Postgres is up |
| Answers always decline | Files have no text, or the query vocabulary doesn't match | See [usage.md](usage.md) — this is usually the right behaviour, not a bug |
| `429` after a burst | Rate limiter, 60 requests per minute per IP | Respect `Retry-After`, or raise `RATE_LIMIT_REQUESTS` |
