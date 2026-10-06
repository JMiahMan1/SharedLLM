# Calibre Library Index

A Calibre library stored in Nextcloud, indexed read-only into the RAG knowledge base so Raven can
answer from the family's books and cite a chapter rather than a character offset.

## What this is

The library lives at `cloud.sumemail.com/Books/Text` and is reached **over WebDAV, read-only**.
Nothing in SharedLLM writes to it. Calibre (or Calibre-Web) remains the only writer of `metadata.db`.

```
cloud.sumemail.com/Books/Text     Nextcloud, one copy of the books
        |  WebDAV GET
        v
storage /index/full                downloads metadata.db, parses it, fetches each EPUB
        |  POST /rag/sync/files     collection "calibre_files"
        v
services/rag                       hybrid BM25 + vector search
```

## How it plugs in

`StorageProvider` (`services/storage/providers.py`) declares three abstract methods, and the storage
service names each collection `f"{provider.kind}_files"` (`services/storage/main.py:138`). Adding a
provider kind therefore routes it into its own RAG collection with **no change to `services/rag`**.

| Piece | Location |
|---|---|
| `ProviderKind` widened to include `"calibre"` | `services/storage/models.py` |
| The provider | `services/storage/providers_impl/calibre.py` |
| Factory branch | `services/storage/providers.py` (`build_provider`) |
| Section-aware extraction | `services/common/epub_text.py` |
| Gateway route | `POST /api/storage/index` with `provider_kind="calibre"` |
| Collection name | derived: `calibre_files` |

## Configuration

| Setting | Where | Meaning |
|---|---|---|
| `calibre_library_path` | Identity `GlobalSetting`, Admin > Settings | Library root inside Nextcloud, e.g. `/Books/Text` |

Blank is a **refusal**, not a fallback. A guessed library path would index the wrong shelf and say
nothing. The request may also carry `library_path` explicitly, which takes precedence.

Nextcloud credentials are the caller's existing ones — the Calibre provider reuses the same
`_resolve_nextcloud_settings` path, so there is no separate credential to configure.

## Triggering a crawl

```bash
curl -X POST https://<host>/api/storage/index \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider_kind": "calibre", "recursive": true}'
```

The crawl runs in the background and returns `202 ACCEPTED`. Chunk metadata carries `title`,
`author`, `calibre_id`, `tags`, `series`, `chapter` and `chapter_ordinal`, so a search hit renders in
Raven's prompt as ` (Source: The Faithful Promiser, 31st Day.)`.

## Why sections, not chunks

`chunk_text` is a fixed 1000-character window, so a book indexed that way yields citations like
"chunk 412" — technically true and useless to a reader. `services/common/epub_text.py` reads the
OPF spine and then splits each document on its **own heading structure**, choosing the level with the
most headings at a depth no deeper than `h4`.

This matters more than it sounds. A Gutenberg-derived book (a large share of any archive.org import)
is a *single* spine document with one `h1` and thirty-one `h2` day markers. Spine-level chunking
gives one 50,000-character "chapter"; heading-aware sectioning gives thirty-two named sections.

Every value written to chunk metadata is a scalar, because `/rag/sync/files` coerces non-scalars with
`str()` — a list arrives back as a quoted string.

## The Bible shelf as a second consumer

The same shelf is also a **source of Bible translations and study Bibles**. The
RAG index above reads `metadata.db` and derives searchable sections; the Bible
importer needs the opposite — the raw bytes of one file, to extract verses and
commentary from it. Both consumers share one setting and one WebDAV path, and
neither of them writes to the library.

```
cloud.sumemail.com/Books/Text
        |  WebDAV GET / PROPFIND
        v
services/storage   POST /providers/list      (browse one folder)
                    POST /providers/fetch     (one file, as bytes)
        |  internal secret, no credentials cross the wire
        v
services/bible     services/bible/library.py  (list, validate, fetch)
                    importer.run(...)          (download -> import folder -> importer)
        |
        v
BibleVersion + BibleVerse + StudyNote rows in bible.db
```

### What was added to storage, and why it lives there

| Piece | Location | Why here |
|---|---|---|
| `StorageProvider.get_bytes(path)` | `services/storage/providers.py` | `get_content` decodes bytes as text, so a PDF or EPUB comes back as a string of replacement characters rather than an error — anything reading it then works from confident mojibake. `get_bytes` returns the real bytes, and returns `None` for "this provider cannot give you bytes", which is a different answer from "the file was empty" |
| `NextcloudStorageProvider.get_bytes` | `services/storage/providers_impl/nextcloud.py` | `NextCloudClient.get_file_bytes`, whose own docstring warns about `get_file_content` |
| `CalibreStorageProvider.get_bytes` | returns `None` | The Calibre provider is an **index**, not a file server. Declining is correct, and `test_a_provider_that_cannot_give_bytes_declines_instead_of_guessing` pins it |
| `ProviderFetchRequest` | `services/storage/models.py` | `provider`, `path`, `max_bytes` |
| `POST /providers/fetch` | `services/storage/main.py` | Returns `{"status":"SUCCESS","path","name","size","content_b64"}`, guarded by `X-Internal-Secret` |

The Bible service browses through the **nextcloud** provider rather than the
calibre provider, because it needs bytes and only Nextcloud serves files. This
keeps the credentials in one place: rotating the Nextcloud password is one edit
in one service rather than one per caller.

`POST /providers/fetch` failures are all distinguishable, because they need
different fixes:

| Status | Meaning |
|---|---|
| **413** | The file is larger than `max_bytes`. Both numbers are named. A silently shortened EPUB would look to an importer like a complete one that ends early, so this is refused rather than truncated |
| **502** | `get_bytes` returned `None`. Names the path and lists all three causes: missing file, wrong credentials, provider cannot return binary |
| **422** | `max_bytes <= 0` — a ceiling of zero is a mistake, not a request for nothing |

### Configuration

`calibre_library_path` is **the same setting** as the RAG index uses. A server
already indexing books for search gets Bible imports for free from the same
folder, and blank is a refusal for both consumers rather than a guess that
reports the wrong shelf as an empty library.

The bible service reads it **from Identity at call time**, so pointing it at a
new shelf in Admin › Settings takes effect without a restart; the storage
service's index reads it through `resolve_runtime_config()`, which does need one.

## What is deliberately not supported

- **Writes.** `write_content` returns a refusal. `metadata.db` belongs to Calibre.
- **PDF and DOCX text.** Only `EPUB`, `AZW3`, `MOBI` and `FB2` are extracted. A PDF-only book still
  appears as a searchable metadata row rather than a failure, and nothing pretends its bytes are text.
  (The Bible importer is the exception, because it has its own PDF reader — see
  `services/bible/pdf_import.py` — and it refuses a PDF whose chapter counts do not match the canon.)
- **Calibre-Web's `/cdb/cmd`.** That endpoint executes arbitrary `calibredb` commands, which is a
  shell on the library. Use `calibredb` deliberately, not by proxy.
- **Annotations and reading progress.** The schema carries both tables; neither is read yet.

## Operational notes

- **`metadata.db` is downloaded once per crawl**, not once per book.
- **Give `services/storage` a volume before a full crawl.** Its `index_checkpoint.json` has no volume
  today, so it is lost on every `up -d --force-recreate` and a 3,000-book run restarts from zero.
- **A full-library crawl is long.** Scope it with `path` (e.g. one author folder) or `force=false`.
- The library directory is skipped by `GLOBAL_SKIP_LIST` only for entries that carry provider
  metadata; a book under an author folder named `lib` is still indexed, and a skip that did happen
  would be logged rather than silent.