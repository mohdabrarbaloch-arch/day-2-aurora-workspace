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

Uploads persist in the `aurora_uploads` volume and the database in `aurora_pgdata`.

## OCR

```bash
sudo apt-get install tesseract-ocr        # Debian / Ubuntu
brew install tesseract                    # macOS
```

Verify with `tesseract --version`. If it is absent, `GET /api/health` reports
`"ocr": "unavailable"` and image uploads still work — they just contribute no text.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SECRET_KEY must be set` at startup | `APP_ENV=production` with no key | Generate one, or run in development |
| Uploads return `413` | File over `MAX_UPLOAD_MB` | Raise the limit, or send a smaller file |
| Uploads return `415` | Content doesn't match an allowed type | Check the magic bytes — renaming `.exe` to `.png` fails by design |
| Images upload but have no text | Tesseract missing, or image has no text | Install Tesseract |
| `"database": "error"` from `/api/health` | Bad `DATABASE_URL` | Check the DSN and that Postgres is up |
| PDF uploads say "no text layer" | Scanned PDF | OCR it first; Aurora does not OCR PDFs |
