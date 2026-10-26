# Usage

## The short version

1. Create an account.
2. Create a workspace.
3. Upload files to it.
4. Ask a question.

## Accounts

Registration returns a JWT immediately, so you are signed in after one request. Passwords need
at least 8 characters; there is no upper bound, but bcrypt truncates beyond 72 bytes — the app
rejects anything longer rather than silently ignoring the tail.

Tokens are stored in `localStorage` by the web client and sent as
`Authorization: Bearer <token>`. They expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default
1440, one day). There is no refresh flow: when the token expires you sign in again.

## Workspaces

A workspace groups a set of files with one conversation. Separate topics into separate
workspaces — the retrieval engine only searches within one, so a narrower workspace gives
sharper answers.

Deleting a workspace deletes its files from disk and its message history. There is no undo.

## Uploading

| Type | Extensions | What Aurora extracts |
|---|---|---|
| Image | jpg, jpeg, png, webp, gif | OCR text, dimensions, dominant colour palette |
| Document | pdf | Text per page, page count |
| Text | txt, md | Full text |

Type is decided by **magic bytes**, not by filename or the declared `Content-Type`. A renamed
executable is rejected with `415`.

Images are re-encoded through Pillow on upload. That normalises colour profiles and **strips
EXIF metadata**, including GPS coordinates — the stored file is not the original.

**Limits:** `MAX_UPLOAD_MB` (default 15), counted while streaming rather than trusted from the
`Content-Length` header.

**Scanned PDFs:** a PDF with no text layer yields zero chunks. The upload succeeds and the asset
is flagged `has_text: false`. Aurora will not invent content for it. OCR it externally first if
you need it searchable.

## Asking questions

The answer stream emits three event types:

```
event: start
data: {"provider": "local", "grounded": true}

event: delta
data: {"text": "Net "}

event: done
data: {"citations": [{"filename": "contract.pdf", "page": 7, "snippet": "Net 30 from invoice date", "score": 4.12}], "grounded": true, "latency_ms": 3.4}
```

`provider` tells you what actually produced the words — `local` means the retrieval engine
composed it by extracting your sentences, not a language model.

### Why an answer sometimes declines

If no chunk clears `RETRIEVAL_MIN_SCORE` (default 0.35), the answer is:

> "I couldn't find anything in your uploaded files that answers that. Try rephrasing, or add
> the document or image that covers it."

with `grounded: false` and no citations. This is deliberate. The alternative — answering from
general knowledge — would make every citation meaningless, because you could no longer tell a
sourced answer from a guessed one.

If that happens, in order of usefulness:

1. **Use the document's vocabulary.** BM25 matches terms, so "payment terms" beats "when do I
   pay".
2. **Check the file has text.** Look for the `has_text` badge in the UI.
3. **Upload the right document** — the obvious one, and the most common cause.
4. **Lower the threshold** if your documents are terse, accepting more marginal matches.

### Streaming over curl

```bash
curl -N -X POST "$BASE/api/workspaces/$WS/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "what is the notice period?"}'
```

`-N` disables buffering. Without it curl holds the stream until it closes and you lose the
effect.

## Keyboard and interface

- **Enter** sends; **Shift+Enter** adds a newline.
- Citation cards are clickable and jump to the source asset.
- The workspace sidebar shows each file's type and whether text was extracted.

## A worked example

```
1. POST /api/auth/register        → token
2. POST /api/workspaces           → workspace id
3. POST /api/workspaces/{id}/assets   (multipart, one or more files)
4. POST /api/workspaces/{id}/messages ({"question": "..."})  → SSE stream
```

That is the whole product. Everything else is listing and deleting.
