# Bible Study — implementation plan

Status: **plan / research complete**. A Bible study experience built into
SharedLLM (desktop + Capacitor mobile from the same React code), using
**blb.org (Blue Letter Bible) as the study-tool base** and patterned on the
Life.Church Bible App (YouVersion): per-user usage tracking, streaks,
achievements, family sharing, plans, quizzes, memorization challenges,
games, and **Jarvis as a study assistant**.

**This is a reading app first.** The product is the reader: open the app, it
is already on the page you left, the text is legible and beautiful, your
highlights and notes are there, and everything else is one tap away and
never in the way. Plans, quizzes, games, achievements and Jarvis hang off
the reader rather than competing with it for the first screen. Every phase
below is gated on the reader feeling finished before the next surface is
built. §"Reading parity with the Bible App" is the acceptance checklist for
Phase 1; nothing else ships ahead of it.

Research date: 2026-10-02. Companion docs:
`docs/ACHIEVEMENTS.md` (points/ledger/sharing), `docs/FAMILY_HUB.md`
(chat/games surfaces), `docs/CHAT_UI_RESEARCH.md` (envelope format),
`docs/GEO_SERVICE.md` (endpoint patterns), `AGENTS.md` (mobile parity,
fail-fast rules).

## Principles

1. **Reading is the product.** Everything else is a satellite. No surface
   may take the first screen, the first tap, or the daily rhythm away from
   reading the actual text.
2. **Reuse, never parallel-build.** Points, achievements, sharing, chat
   cards, notes, Nextcloud storage, RAG, push notifications, scheduled jobs,
   workspaces, the TTS engine and the LLM proxy all exist. The Bible
   feature is a new *domain*, not a new platform — and not a new way to
   store a note, award a point, schedule a reminder or notify a phone.
3. **Touch every existing service, on purpose.** §"SharedLLM service
   integration map" names the service, endpoint and payload for each
   integration. A new capability is only allowed to be new if no existing
   service already does it.
4. **blb.org is a study-tool base, not a content API.** Blue Letter Bible
   publishes no public REST API — only free web tools (ScriptTagger hover
   embeds, search forms). We deep-link into blb.org for Strong's, lexicons,
   concordances and commentaries, and vendor public-domain Scripture text
   ourselves so reading works offline with zero per-call dependency.
5. **Opt-in or invisible.** Reading activity is private by default, exactly
   like steps/location (`UserActivitySharing`). Family visibility is a
   deliberate grant per user, per scope.
6. **Fail fast, never fall back.** No upstream (api.bible key, BLB base
   URL, devotional directory, database URL, TTS engine/voices, LLM
   provider) ever silently degrades to a hardcoded literal or a no-op.
   Unconfigured features show a visible message naming the missing
   setting.
7. **Mobile parity is the bar, not the exception** (AGENTS.md). Every
   surface ships phone-first; desktop density is a layout variant of the
   same components.

### The hierarchy, stated once

```
Read  ← the product. offline-first, fast, typographically serious, remembers where you were
 ├─ Marks        highlights / bookmarks / notes   (syncs across devices)
 ├─ Plans        a daily rhythm to give the reading a spine
 ├─ Study        BLB link-outs, cross-refs, Jarvis explain-this-passage
 ├─ Memorize     SRS-lite recall ladder (a reading habit, not a course)
 ├─ Play         daily quiz / family trivia (competition as seasoning, never the meal)
 └─ Progress     streaks, stats, achievements, sharing controls
```

A user who only ever opens Read and never touches a single other tab must
feel the app is complete. That is the test for scope.

## Research findings

### YouVersion / Bible App (the "A LOT like" target)

Sources: youversion.com/bible-app, help.youversion.com (Plans, Streaks,
Plan with Friends), blog.youversion.com/getstarted.

| Feature | How they do it | What we take |
|---|---|---|
| **Plans** (devotionals/reading plans) | Duration in days, daily excerpts, sample, rating, "Save for Later", start-by-myself or **With Friends** (max 300, fixed start date, per-day progress visible to the group, private discussion area per day), "Catch Me Up" when behind, offline access, daily reminders | Plans with day checklists, progress bars, streak, catch-up, family plans with per-member progress + Talk-thread discussion |
| **Streaks** | App-Open streak (works offline, counts on reconnect) and Guided-Scripture streak (requires connectivity, resets if a day is skipped); reminder push at 7 pm local if not opened | App-open streak (offline-tolerant) + reading streak (≥1 chapter/day) + plan streak; reminder via the automation service |
| **Personalization** | Highlights (colors), Bookmarks, Notes — all synced to the account, visible on any device | Same triad, per-user, in our SQLite |
| **Verse of the Day** | Daily verse + artist image, "See All" history, shareable | VOTD from the corpus (deterministic by date), share card |
| **Verse Images** | Tap verse → scripture-art card → share to SMS/WhatsApp/etc. | Share card rendered in-app + native share sheet (Capacitor) |
| **Friends feed / profile timeline** | Friends' activity (plan days, shares) in Home feed; activity timeline on profile | Opt-in activity feed (reuse geo `/activity/feed` pattern) scoped to `bible` |
| **Versions** | 1,200+ versions/900 languages; compare in parallel | Vendored public-domain set + optional api.bible upstream; side-by-side compare |
| **Audio** | Audio Bibles, listen offline | Recorded audio is licensed content (out of scope), but **our TTS engine already narrates Scripture**: `services/execution/tts.py` has a `BIBLE_BOOKS` table + `_expand_scripture_refs()` that turns "John 3:16" into spoken prose. Read-aloud is Phase 1, not Phase 7 |
| **Home screen / daily rhythm** | Verse of the Day at the top of Home; 7pm reminder; streak in the top bar | `bible_daily` widget + continue-reading, and the same reminder plumbing as every other notification |

### Blue Letter Bible (blb.org) — the requested base

Sources: blueletterbible.org/webtools, /webtools/BLB_ScriptTagger.cfm,
blbclassic.org/freeoffer.cfm, study.bible/aboutBLB (researched 2026-10-02).

- **No official REST/JSON API exists.** Confirmed: the only documented
  integration surface is link/embed-based:
  - **BLB ScriptTagger** — free drop-in JS
    (`blueletterbible.org/assets/scripts/blbToolTip/BLB_ScriptTagger-min.js`)
    that scans a page for references (`Rom 1:16-18`, `Jhn 3:16`,
    `Deut 8:2-6, 10`, `Genesis 1:26-28; 3:15`, with optional translation
    abbreviation) and renders a hover tooltip with the verse text + a link to
    study at BLB. Config: `BLB.Tagger.Translation` (default NKJV),
    `HyperLinks` (`all|hover|none`), `HideTranslationAbbrev`,
    `TargetNewWindow`, `DarkTheme`, `Style` (`line|par`),
    `NoSearchTagNames`, `NoSearchClassNames`. **Tooltip caps at 7 verses** —
    longer ranges get a "More »" link to the chapter on blb.org. WordPress
    plugin available.
  - **BLB Search Forms** — a documented `GET` form to
    `blb.org/search/preSearch.cfm` with `Criteria` (query), `Version`
    (KJV, NKJV, NLT, NIV, ESV, CSB, NASB20/95, LSB, AMP, NET, RSV, ASV,
    YLT, DBY, WEB, HNV, VUL, NAV, WLC, LXX, mGNT, TR, SVD, BES, RVR09/60,
    BBE, CHT, EM, KOR, LS, LUT, RST, SE) and `cscs` (optional verse range).
  - **Deep study tools** (web only): Strong's numbers, Hebrew/Greek
    lexicons, concordances, Easton's Bible Dictionary, ISBE, Treasury of
    Scripture Knowledge, Harmony of the Gospels, inline interlinear,
    8,000+ text commentaries, ScriptureMark markup studies, BLB Institute
    courses. **BLB also publishes daily reading plans** (`/dailyreading/`:
    Canonical, Blended, Chronological, Historical, OT+NT, Five-Day) and
    devotionals (Day by Day by Grace, Morning and Evening, Daily Promises,
    Faith's Checkbook) — good link-out content for our Plans section.
  - **Deep-link URL shapes** (for our link-outs): passage
    `blb.org/{book}/{chapter}/{verse}` (e.g. `/jhn/3/16/`), lexicon
    `/lexicon/h3068/KJV/`, interlinear
    `/tools/interlinear/tr/jhn/1/1-51/`.
- **Unofficial scrapers exist but are off-limits**: GitHub downloaders that
  scrape BLB pages to JSON/TXT and third-party scraping APIs (e.g. Spider
  Cloud). They violate BLB's terms, are brittle, and would silently serve
  stale/copyrighted content — the repo's fail-fast doctrine forbids
  depending on an undocumented endpoint.
- **Consequence for the plan**: we do **not** scrape or reproduce BLB
  content (copyrighted). We (a) deep-link every study affordance to the
  right blb.org URL, (b) implement our own ScriptTagger-style reference
  hover for Jarvis answers and notes (same 7-verse cap, then "More »" to
  BLB), and (c) vendor public-domain text for the reader itself.

### Scripture text sourcing

Shipped in `services/bible/corpus_manifest.json`. Public-domain text is
downloaded and checksummed into the service's SQLite; licensed translations are
declared but never bundled, because a household server is not the vendor's
licensee.

| Translation | Rights holder | `license_class` | How it gets installed |
|---|---|---|---|
| KJV, ASV, WEB | public domain | `public_domain` | `python -m services.bible.import_corpus --manifest` (downloads, verifies the recorded sha256) |
| NKJV | Thomas Nelson | `licensed` | The study EPUB the family already owns, imported with `--import-notes` |
| ESV, NIV, NLT | Crossway, Biblica, Tyndale House | `licensed` | Local PDF/EPUB the family provides; text is never redistributed by us |

`GET /versions` returns the manifest joined with what is actually installed, so
the version picker can offer a translation that is *catalogued but absent*
along with the reason, instead of silently hiding it or failing on selection.

| Source | License | Role |
|---|---|---|
| **Vendored corpus** (KJV, ASV, WEB — US public domain) | Public domain | Primary reader text, offline, zero runtime dependency |
| **Family-owned licensed files** (NKJV, and ESV/NIV/NLT if the family supplies them) | Publisher copyright, non-redistributable | Read and study inside the household only; `license_class: "licensed"` marks the rows as do-not-redistribute |
| **api.bible** (scripture.api.bible) | CC/public-domain bibles free for non-commercial; per-translation commercial license otherwise; `api-key` header; REST: `/v1/bibles`, `/bibles/{id}/books`, `/chapters/{id}?content-type=json`, `/search?query=` | Online provider for extra translations, **declared in the manifest** (`providers`) rather than hard-coded, and keyed by Identity `GlobalSetting` `bible_api_key` — **no default, fails loudly when unset**. **The API host is `api.scripture.api.bible`, not `api.bible`** — the latter is the marketing site and 404s every `/v1/*` path. `services/bible/providers.py` fetches a translation on demand at import time and caches it like any other translation; **nothing on the read path touches the network**. Verified live: 40 English translations on a free key, including **NIV** and **NLT**; **ESV and NKJV are not in the catalogue** and still need publisher files |
| **BibleGet I/O** (query.bibleget.io) | Open; no key, but `appid` param required; caching expected; rate-limited per IP; CORS-enabled; `/v3/quote`, `/v3/search/keyword`, `/v3/search/semantic` (vector), `/v3/search/similar` (verses like a reference) | Optional verse-fetch + **semantic/similar search for Jarvis cross-references** |
| **NET Bible labs API** (labs.bible.org/api/?passage=Lk%202:1&formatting=full&type=json) | NET copyright, API use must comply | Optional alternate upstream |
| **Digital Bible Library** (api.library.bible) | License agreements per content; org-scoped API keys | Long-term option for many languages |
| **blb.org** | Site content copyrighted; web tools free for link-out | Deep-study destination only |

### Extracting verses and study material from a file

Two importers, both driven by a manifest and both refusing anything short of a
complete Bible. `parse_source` requires the **exact** canonical chapter count for
all 66 books, so a truncated or mis-paginated file is an error, never a Bible
with holes in it.

| Importer | Input | What it takes |
|---|---|---|
| `services/bible/import_corpus.py` | JSON `[{"name", "chapters": [[verse, …], …]}]` | Verses only |
| `services/bible/import_pdf.py` | A PDF | Verses. Detects two-column layouts, ignores running heads and page numbers, and only accepts a verse number that is the *expected next* one — which is what stops a page number from becoming a verse |
| `services/bible/import_epub.py` | An EPUB | Verses **and** study notes |

The EPUB route is what makes a study Bible a study Bible here. It walks the
documents in spine order and, for every element carrying a verse anchor
(`id="vBBCCCVVV"`, which encodes book/chapter/verse), collects the text from that
anchor to the next. Verse continuations in sibling paragraphs are recognised by
class so poetic books (`Psalms`, `Job`, `Song of Songs`) do not lose a line, and
every paragraph class that was *declined* — footnotes, commentary, sidebars,
concordance, glossary, front matter — is reported by name in the import output,
so "we left this out" is always visible rather than silent.

The online provider returns the same idea in a different dialect: a chapter is a
nested node tree in which a `verse` tag carries **only the verse number** and the
words follow in *sibling* nodes tagged with `attrs.verseId`. So a provider verse
runs from its marker to the next marker, exactly as an EPUB verse does. Two
things there are load-bearing and are covered by tests: a chapter declares its
own `verseCount`, and a chapter that returns fewer verses than it declared is
**refused and not written** rather than stored quietly short; and an abbreviation
that collides with another book (`JUD` is listed for *Jude*, not *Judges*) loses
to the longer book name, because first-wins would file Judges under Jude and lose
a book.

One rule is deliberately *relaxed* for the provider and kept strict for files. A
blank verse in a PDF or EPUB means text was lost on the way in, so `parse_source`
refuses it — that is what catches a truncated or mis-paginated file. A provider is
authoritative about its own versification and simply omits verses that textual
criticism treats as later additions: NIV2011 has **no Matthew 17:21** at all, nor
Mark 7:11–12. So `parse_source(..., allow_gaps=True)` is set **only** on the
provider path. The blank slot is kept as `""` so every verse still carries its
printed number, `corpus.omitted_verses()` names each one, and the import log
reports them (`43 verses are absent from this translation by design (Matthew
17:21, Mark 7:11, …); they keep their printed numbers`). `BibleVerse` is keyed on
`(version, book, chapter, verse)`, so a gap shifts nothing — it only means asking
for Matthew 17:21 returns "not in this translation" rather than silently landing
on the wrong verse.

A whole Bible is roughly **1,189 chapter requests** plus one for the book list,
and api.bible's free tier allows about **5,000 requests a month**. An import
therefore costs a quarter of the allowance, which makes cost a first-class part of
the operator's workflow rather than something to discover afterwards:

- The cost is **asked for before it is spent.** `GET /admin/providers/{code}/estimate?translation_id=`
  returns how many of a translation's chapters are already cached and how many
  requests remain, and the Install button is **disabled when the run would exceed
  the budget**.
- A run that needs more than `BIBLE_PROVIDER_CALL_BUDGET` **new** requests is
  refused before a single request is made, naming both numbers and stating that
  nothing was fetched or written. A warm cache costs nothing against the budget.
- **"Test one book first"** fetches Genesis 1 alone, parses it, shows the text and
  installs nothing — so a parsing change can be checked for one request instead of
  1,189. The chapter it fetched stays in the cache, so the real run does not pay
  for it again.
- A fetch that still runs into the cap is refused cleanly: the operator sees the
  API's own `Monthly limit exceeded. Please upgrade your plan.` and the `ImportRun`
  records it, instead of installing half a Bible.

### The provider cache

Every chapter that comes back from a provider is written to
`BIBLE_PROVIDER_CACHE` **before** it is parsed, and it is kept **write-once**.
Scripture does not change, so a chapter fetched once is never fetched again — not
on a retry, not next month, not when a translation is re-imported because a study
Bible was added. The only way to change a cached entry is to delete it.

Four properties make that safe rather than merely fast:

- **The raw payload is cached, not the parsed verses.** Scripture text is stable
  but the provider's *framing* is not, so the bytes are kept and a parser fix can
  be re-applied for free instead of re-spending the allowance.
- **Identity deliberately excludes the API key.** A translation's directory is
  `sha256(provider | base_url | dialect | translation_id)`, so rotating a key keeps
  the whole cache. Changing the dialect (`CACHE_VERSION`) or the host gets a *new*
  directory rather than quietly serving text parsed the old way.
- **A read verifies before it serves.** A hit counts only when the stored chapter id
  matches what was asked for and the content is non-blank; anything else is a miss,
  never wrong verses.
- **Writes are atomic** (temp file + `os.replace`), so a killed process cannot
  leave a half chapter that later reads as truth.

The cache lives beside the database rather than inside it, so it survives a wiped
`bible.db`. A fully cached translation — book list included — costs **zero**
requests, and a run killed at book 40 costs nothing at all, which is the failure
that previously wasted whole allowances.

The provider is asked for **`content-type=json`**, which returns a node tree: a
`verse` tag whose only content is the verse number, with the actual words in
sibling nodes carrying `attrs.verseId`. So a provider verse runs **from its marker
to the next marker** — the same rule the EPUB importer already uses — and
`_verse_texts()` walks the tree maintaining that open verse.

The plain-text form (`content-type=text`, one string with `[n]` markers) was tried
and **rejected**, which is worth recording because it looks like the safer choice.
api.bible tags a *section heading* with the next verse number, so `[26] The
Authority of Jesus Questioned` invents a verse that does not exist and appends the
heading to the previous verse's text. Comparing two whole-Bible runs showed **1,204
polluted verses** in the text parse against 16 honest blanks in the node parse, and
the two "extra" verses it appeared to recover were headings (`Mark 11:26`,
`Matt 17:21`). The node tree is the only trustworthy dialect.

Three things are refused rather than trusted: verse numbers that do not run
`1, 2, 3…` (the signature of a mangled response, which would misalign every later
number), a Bible missing any of the 66 books, and a chapter whose text cannot be
reconstructed. The payload's own `verseCount` is **not** used as a check, because
it is not the verse count on this host: Numbers 1 declares 42 and has 54 verses.

Book matching also needed care: api.bible's `books` list has **no chapter list**, so
chapters are enumerated from *our* canonical table rather than trusted from the
response, and a book is matched on its **longest** resolving name rather than the
first. `JUD` is a listed abbreviation of *Jude*, not *Judges*, so first-wins filed a
whole book under the wrong one.

Study notes are then joined back to the verses through the anchors the text
itself carries: a link to `com21003001` in a verse paragraph points at the
commentary block with that id in another document, and extended commentary
(`comx`) is found through the id recorded in its surrounding HTML comment. The
result is stored in `StudyNote`, keyed by translation, study Bible, book,
chapter, verse and kind — **never merged into `BibleVerse.text`**.

A note's identity includes the study Bible it was written for, because
several study Bibles sit on the same words: the Nelson and the MacArthur
commentaries on NKJV are different arguments about the same verses, and one
must never quietly replace the other. That is why the corpus has two axes
rather than one:

| Axis | Code | Meaning |
|---|---|---|
| Translation | `BibleVersion.code` (`nkjv`) | The words. Imported once, keyed by translation alone, never duplicated |
| Study Bible | `BibleEdition.code` (`nkjv-tmn`, `nkjv-macarthur`) | Whose commentary. Any number of these per translation |

Every installed translation always gets at least one edition — an implicit
text-only row named after the translation — so "this translation has no
notes" is an answerable question rather than an empty list. The **edition** is
the default for a translation, because defaulting to "the one with the most
notes" would silently change what a reader sees after a re-import.

### One translation is primary

The manifest marks exactly one translation `primary`, and the loader refuses a
manifest that marks two. Reading defaults to it: `corpus.default_version_code()`
returns the primary when it is installed, otherwise the installed translation
with the most verses, otherwise `""` — which the service reports as a 503 with
its own message rather than picking something. `_default_version` and the verse
of the day both go through that one function, so the daily card cannot quietly
come from a different translation than the reader.

NKJV is the primary. It is the one the family owns, and the one carrying the
study notes.

### Installing more: Admin › Bible

Four ways in, because the family's sources differ, all through
`services/bible/importer.py` — one code path, with the three CLIs reduced to
thin callers:

| Way | Route | What it needs |
|---|---|---|
| Online provider | `GET /admin/providers/{code}/estimate` for the cost, `GET /admin/providers/{code}/translations` to list, `POST /admin/imports` with `provider`/`provider_id` to install | `bible_api_key` (or whatever the provider declares). The catalogue is a *separate* call so listing it never needs the network and an unreachable provider produces one message in one place. Install is disabled until the estimate is in and the run fits the budget |
| **The Calibre shelf in Nextcloud** | `POST /admin/library` with `path` to browse, `POST /admin/imports` with `library_path` to install | `CALIBRE_LIBRARY_PATH` and `BIBLE_IMPORT_DIR`. Browse first, then install a file you can see — see below |
| Upload from the browser | `POST /admin/imports/upload` (multipart) | `BIBLE_IMPORT_DIR`. The extension is checked **before** anything is written; a 256 MB limit streams in 1 MB chunks and removes the partial file if it trips |
| A path on the server | `POST /admin/imports` with `source_path` | The path, for a file already downloaded somewhere the service can reach |

Every run is recorded in `ImportRun` **including the failures** — the refusals
are the interesting half ("Exodus is missing from that PDF", "That code is not
in the manifest") and the report is returned with a 422 so a browser fetch
cannot treat a refusal as a success. No scripture text is stored in the history.
Import routes are **admin-only** at the gateway (`_require_admin`), because
installing a translation writes files on the family's own server.

### The Calibre shelf as a translation source

The family already keeps books in a Calibre library inside Nextcloud, and that
library is already indexed read-only into RAG (see `docs/CALIBRE_LIBRARY.md`).
The shelf is therefore a **first-class import source**, not a workaround: the
admin panel opens it, walks folders, shows which files can be read, and installs
one — with no credentials in the browser and no command to type.

```tsx
Admin › Bible → Browse /Books/Text
  ↑ Up one level
  📁 Thomas Nelson (3198)
  📕 The NKJV Study Bible - Thomas Nelson.epub      Ready to import as epub      [Install]
  📄 The NKJV Study Bible - Thomas Nelson.docx      Not a Bible format (docx)
```

The design is deliberately boring: **download the file, then import it exactly
as if it had been uploaded.**

| Step | Where |
|---|---|
| Browse one folder over WebDAV | `services/bible/library.py` `LibraryClient.browse` → `POST {storage}/providers/list` |
| Fetch one file as raw bytes | `LibraryClient.fetch` → `POST {storage}/providers/fetch`, staged `<import_dir>/nextcloud-<folder-hash>-<filename>` |
| Import it | `importer._materialise_from_library` → `plan._replace_source(fetched)` → the ordinary `pdf`/`epub` path |

There is exactly **one importer to trust, not one per source**: a shelf EPUB is
parsed by the same code that parses a browser upload of the same book, so a
study Bible keeps its commentary whichever way it arrived.

Three decisions worth recording:

- **Bytes travel through `services/storage`, never through the bible service
  directly.** Storage already owns the Nextcloud credentials, so rotating the
  password is one edit in one service instead of one per caller.
- **An unreadable file is shown, not hidden.** A shelf holding one unusable
  DOCX should not look like a shelf with a hole in it, and the reason belongs
  next to the file rather than in a log line an operator never sees.
- **The fetched name carries a folder hash.** Two shelves can both hold a file
  called `Bible.epub`; without the hash one silently overwrites the other. The
  real filename stays at the end so it is still recognisable in the import
  folder.

`CALIBRE_LIBRARY_PATH` unset is a **visible** state, not a silent skip: the
Browse button is disabled and Admin › Bible shows `calibre_library_path` with
the setting name and a suggested value. Guessing a shelf would report the wrong
folder as an empty library, which is worse than saying nothing.

## Decisions

| Need | Decision | Why |
|---|---|---|
| Scripture text | Vendor public-domain corpus into the new service's SQLite, and import licensed family-owned files locally | Offline, no per-call cost, no license risk; BLB stays link-out |
| Extra translations | Family-supplied PDF/EPUB **or** the api.bible provider, declared in `corpus_manifest.json` and installed from Admin › Bible | Publisher text is read inside the household, never redistributed. The provider is configuration, not code, and needs a key before it is offered — an unconfigured provider is shown with the setting to set rather than hidden |
| Primary translation | Manifest marks one translation `primary`; reading and the verse of the day both default to it | "Whichever translation sorts first" is not a decision anyone made. One marked translation makes the default explicit, and `default_version_code()` falls back to the richest installed rather than to a guess |
| Study material (commentary, footnotes, introductions) | Separate `StudyNote` table, joined to verses on demand via `GET /study/notes`. A verse that carries notes is marked in the reader; the notes themselves load only when asked | A reading app that mixes a commentary into the text is worse at reading. Keeping them apart is what lets one reader serve both a plain KJV and a study NKJV |
| Several study Bibles per translation | Translation and study Bible are separate columns (`BibleVersion.code` vs `BibleEdition.code`); the reader picks which one explains the text | The Nelson and MacArthur study Bibles are both NKJV and disagree. Collapsing them into one `version_code` would make re-importing one delete the other's notes |
| Notes from another translation | Opt-in `cross_version`, default **off**, remembered per user as `cross_version_notes`. Off means only notes written for the translation in hand; on adds the others, each labelled with its translation and study Bible | A note on the NIV wording is not a note on the NKJV wording. Merging them silently would put arguments about different words under the same verse. Off by default; the toggle names exactly which study Bibles it would add |
| Study depth (Strong's/lexicon/interlinear) | blb.org deep links + ScriptTagger-style hover | BLB explicitly offers this; reproducing their content is not |
| Verse fetching beyond the vendored corpus | api.bible at `api.scripture.api.bible` (key-gated) or BibleGet (`appid`-identified, cached) | Both documented and reachable; never an undocumented BLB scraper. Which translations a key unlocks varies by licence, so the catalogue is fetched rather than assumed |
| Jarvis cross-references | BibleGet `/v3/search/similar` + corpus-internal cross-refs | "Verses like this" is a study superpower and needs no model call |
| Per-user state | New `services/bible/` with SQLModel/SQLite + Redis day-buckets | Mirrors geo; keeps Identity from growing a second activity schema |
| Achievements | Data-driven `bible_achievements.json` + rules engine, same pattern as `services/geo/achievements.json`; **derived, not stored** | Bug fixes instantly correct history; new badges need no deploy |
| Points/currency | Award into the **existing** `geo:points:{user}` ledger via geo's internal endpoint (reason `achievement`/`game`) | Two currencies, one ledger — chores, fitness and Bible reward the same vault, stars mirror to Skylight |
| Family sharing | Extend `UserActivitySharing` scopes with `bible`; reuse audience enforcement (circle/users, 404-when-not-consented) | One consent boundary, already tested; a second source of truth for "may I see this user" is how checks drift |
| Family plans | New `PlanGroup` table (host + members, per-day per-member progress); discussion rides **Nextcloud Talk** via the typed chat envelope | Play happens where the family already is; no second inbox |
| Quizzes/games | Templated deterministic generators as data (`services/bible/games/*.json`, like `family_games.py`); Jarvis-generated questions only when the server can verify the answer key against the corpus | A game that hallucinates a wrong answer breaks trust; the server is the validator |
| Memorization | SRS-lite interval ladder + recall levels, per-verse state | Simple, explainable, no ML needed |
| Jarvis study assist | Existing gateway `/v1/chat/completions` with a study system prompt; answers post-processed to resolve scripture references into chips | Reuses Raven/LLM plumbing; reference chips give the ScriptTagger experience |
| Read-aloud | **Existing** `POST /execute/tts` (Kokoro + Edge-TTS) — `_expand_scripture_refs()` already narrates "John 3:16" as prose, and SSMD single- and double-pipe pauses give a good reading cadence. Gateway gains a thin `/api/bible/speak` proxy that returns cached WAV bytes | We own no new engine and no audio licenses; the one missing piece is a gateway route. "Play on the kitchen speaker" rides the existing `/execute/ha_service` `tts.piper` path |
| Note storage | **Nextcloud, via the Notes handler.** `VerseMark.note` stores only a ref + the Nextcloud path; the body is written by the existing `/execute/note` handler into `Notes/{Title}.md` or `{category}/{Title}.md` | Bible notes are visible in Notes, searchable in RAG, shareable with the family folder, and editable by hand. Two copies of a note is how they drift apart |
| Sharing controls | Extend `ActivitySharingPanel` with a `bible` scope (4th checkbox next to `totals`/`workouts`/`achievements`) | Private by default; audience `circle`/`users` already enforced server-side |
| Reminders | **Automation** daily job per user + **telemetry** `send_to_user` for delivery, in-app outbox as the durable record | No new notification infra; the 7pm nudge and the reminder are the same code path as every other notification |
| Study memory | **RAG** ingest of promoted notes and study threads, with a stable citable `id` per lesson (`handlers/learning.py` pattern) | "Show me where we already talked about this" is the study superpower, and the assistant should not re-teach what the family already covered |
| Study workspace | **workspace_runtime** opt-in repo for `study/journal`, `memory/sets/*.json`, quiz results | Lets a family diff/version their own study material; skipped with a visible note when no workspace is enabled |
| Home entry point | `bible_daily` dashboard widget + continue-reading, mirroring how Home opens with Verse of the Day | The reader must be one tap from Home, the way the Bible App opens on the text |
| UI surface | New `/bible` page + `bible_daily` dashboard widget | One page, one widget; nav wired in Sidebar/BottomNav/Header like every feature |

## SharedLLM service integration map

Every existing service, and exactly what the Bible feature asks of it. No
row is aspirational: if the service already does the job, we call it; if
it doesn't, we say what changes. "Change required" rows are the concrete
edits to existing files (collected in §"Changes to existing code").

| Service | What the Bible feature uses it for | Endpoint / mechanism | Change required |
|---|---|---|---|
| **gateway** (`:11435`) | Every `/api/bible/*` route as a thin BFF proxy (identity resolution → `X-Internal-Secret` → JSON pass-through); read-aloud proxy to execution TTS; share-card rendering proxy | Copy the `/api/geo/achievements` proxy pattern verbatim | New route block per endpoint; **no generic TTS proxy exists today**, add `/api/bible/speak` (§Changes) |
| **identity** (`:8001`) | User identity; `bible_svc_url`; `bible_api_key`; `blb_base_url`; reading preferences survive a service restart via `GlobalSetting`; the `bible` sharing scope; widget layout rows | `GET/PUT /api/widgets/settings`, `/api/global-settings`, `GET /api/internal/activity-sharing`, `PUT /api/users/me/activity-sharing` | New `GlobalSetting` keys; `ActivitySharingPanel` gains a 4th scope entry; identity `test_activity_sharing.py` extended for `bible` |
| **geo** (`:8009`) | **Points currency.** Every Bible award and every game score is banked in the existing `geo:points:{user}` ledger, which already mirrors to Skylight stars. One vault, three sources (chores / fitness / Scripture) | `POST /api/geo/stars` (`reason: achievement` \| `game`) | None — call it. Skylight write-through is reported, not assumed, so a Skylight outage can't lose an award |
| **geo achievements engine** | The *pattern* we copy for `bible_achievements.json`: derived-not-stored, `id`/`name`/`description`/`points`/`rule{type,value}`, computed from day-buckets, banked on first read so `earned_on` is stable | `services/geo/achievements.py` + `achievements.json` (19 entries, 9 rule types) | New file `services/bible/bible_achievements.json` + rules engine in the bible service |
| **geo sharing enforcement** | Cross-user reads (family plan board, "what's Dad reading?") must pass the same consent gate: 404 for a non-consented user, feed omits non-consented, never errors | `GET /api/internal/activity-sharing` (audience `circle`/`users`, `user_ids`, `share[]`) | `share[]` gains `"bible"` |
| **execution** (`:8003`) | **TTS read-aloud** (Phase 1): `POST /execute/tts` returns `audio_base64` + `mime_type: audio/wav`, and `_expand_scripture_refs()` already speaks references naturally; voices list for the reader's voice picker; **Nextcloud notes**; **family games kit**; Nextcloud/ABS wrappers for shared folders | `POST /execute/tts`, `GET /execute/tts/voices`, `POST /execute/ha_service` (`tts.piper` playback), `/execute/note`, handlers `family_games.py` | None — call it. Missing engine/voices raise `FileNotFoundError("Kokoro voices missing")` → surfaced as a visible reader state, never a silent no-op |
| **execution → Nextcloud** | **Study notes are Notes.** A highlight or a Jarvis answer can be promoted to a real note in Nextcloud with one tap, in the same folder structure the family already browses; reading history can export to `/reading/journal` markdown | gateway `/api/communication/notes/{create,append,list,read}` → execution `/execute/note` (`storage=nextcloud`, `Notes/{Title}.md` or `{category}/{Title}.md`) | None — call it. The bible service stores only the *index* (ref + Nextcloud path); the note body stays in Nextcloud so Notes and Bible never fork a copy |
| **execution family games** | **Talk games**: a family can run a Bible trivia round in the chat room where they already are, leaderboard in cards, 1⭐ per correct | `handlers/family_games.py` + gateway `POST /api/communication/talk/game` | Bible question set as data (`services/games/bible_trivia.json`); answer validation against the corpus before a question is ever offered |
| **family chat / Talk** | **The Bible happens in the family channel.** Achievement unlocks, plan-day completions, verse shares, quiz results and daily Verse of the Day all land as typed cards — no second inbox, no notification fatigue | Typed envelope (`src/lib/chatEnvelope.ts`), execution action `post_card`, gateway `POST /api/communication/talk/card` (`card_kind`/`card_title`/`card_detail`/`card_stars`/`card_stats`) | New `card_kind` values: `bible_achievement`, `bible_plan_day`, `bible_verse`, `bible_quiz`. Silent when `FAMILY_CHAT_TOKEN` is unset (existing `announce_awards` behavior) |
| **storage** (`:8005`) | The vendored corpus can also live as Nextcloud files (family-readable, backup-able, editable by hand); family Bible folder sharing | WebDAV provider + indexer | Optional: Phase 7 "corpus in Nextcloud" for multi-instance installs. Corpus in SQLite is the default; no change required |
| **rag** (`:8004`) | **Study memory.** Promoted notes, plan reflections and Jarvis study threads are ingested so the assistant recalls what *this family* has already studied. Search across collections (`collection_name: "all"`, hybrid BM25+vector) powers "show me where we talked about patience" | `POST /rag/ingest`, `POST /rag/search` (`collection_name`, `alpha`, `use_rrf`), `/rag/learning` | Ingest compact JSON with a stable citable `id` (`lesson-<sha1[:10]>`) exactly as `handlers/learning.py` does, so mission-style prompts can inject one short line per lesson |
| **automation** | **Reminders + Daily Refresh.** One daily job per user: morning plan/verse-of-day, 7pm "you haven't opened the app" nudge (YouVersion parity), Sunday weekly-memory review, plan-day nudges | Redis timer scheduler, `timer:{user_id}:{timer_id}` | **Required change**: `_resolve_dispatch` allowlists `DISPATCH_TARGETS = {execution, identity, geo, rag}` — add `bible` |
| **telemetry push** | Delivery for every reminder, plus achievement/quiz/plan notifications on the phone | `send_to_user(user, title, body, data)` (webpush VAPID, FCM fallback), in-app outbox `tel:notify:{user}` (rpush) | None — call it. Push failure never loses the notification (it is already in the outbox) |
| **workspace_runtime** (`:8007`) | **Study workspace.** An opt-in sandboxed git repo per workspace where the Bible feature writes machine-readable artifacts — `study/journal/`, `memory/sets/*.json`, quiz results — so a family can diff, review and version their own study material | `/workspaces`, `/files/read`, `/files/write`, `/files/list`, `/workflow/write-sync-commit`, `config/workspaces.json` | None — call it. Opt-in per workspace (owner + "share with all users" already in `Workspaces.tsx`); skipped with a visible note when no workspace is enabled |
| **logging** (`:8006`) | Every award, star grant, plan completion, quiz attempt, Jarvis validation rejection and failed upstream is a structured log line; `blb_link_tap` analytics | existing structured logger | None |
| **control_plane** (`:8008`) | Deploy/health parity for the new service — it is a first-class citizen, not a sidecar | standard image/healthcheck flow | None beyond the standard compose entry |
| **ui / Capacitor** | Desktop + Android + iOS from one React tree; native share sheet for verse images; home-screen widget; haptics; biometric unlock; command palette | `App.tsx` routes, `Sidebar`/`BottomNav`/`Header`, `useHaptics`, `Capacitor.isNativePlatform()`, `ProtectedRoute`/`BiometricAuthModal` | New `/bible` route + nav entries; `bible_daily` widget registration |
| **arcade** (`docs/ARCADE_INTEGRATION.md`) | Existing admin-curated arcade titles stay as-is; a Bible "kitchen-table" round is available in the Family → Games tab, not bolted onto the arcade catalog | Alpaca arcade + planned `services/games/*.json` state in Redis | Bible question set as data only |

### Integration rules that fall out of this map

- **One ledger, one inbox, one notes store, one scheduler, one push path.**
  Points go to geo; conversation goes to Talk; note bodies go to Nextcloud;
  reminders go to automation; delivery goes to telemetry. The Bible service
  owns *reading state and reading events* and nothing else.
- **The bible service never calls an external API on the read path.**
  `api.bible`/`BibleGet`/`blb.org` are enrichment, reached only when the
  user asks for something outside the vendored corpus.
- **Content never enters a no-opt-in store.** Events carry a reference
  (`John 3:16`, `rom`, `quiz_correct`), never note text or highlights —
  Identity's `FeatureUsage` is metadata-only by design.
- **Every optional integration degrades loudly.** Unconfigured → visible
  "not configured: `<setting>`" state naming how to fix it, never a silent
  skip and never a canned substitute.

## Backend architecture

### New service: `services/bible/` (port 8010)

Same skeleton as geo: FastAPI, `lifespan`, `X-Internal-Secret` middleware
(global require), `info_router` from `services.shared.info_endpoint`,
SQLModel/SQLite at `/data/bible.db` (new volume `./data/bible:/data`),
Redis for day-buckets/streaks.

Compose entry (copy the `geo` block): image
`ghcr.io/jmiahman1/sharedllm-bible:latest`, env `INTERNAL_SECRET`,
`FERNET_KEY`, `NETWORK_MODE=bridge`,
`IDENTITY_SVC_URL=${BRIDGE_IDENTITY_SVC_URL}`,
`GEO_SVC_URL=${BRIDGE_GEO_SVC_URL}`,
`EXECUTION_SVC_URL=${BRIDGE_EXECUTION_SVC_URL}` (TTS read-aloud),
`REDIS_URL=${BRIDGE_REDIS_URL}`,
`GATEWAY_INTERNAL_URL=http://gateway:11435`, `TZ=America/Phoenix`,
volume `./data/bible:/data`, `depends_on: redis`.
Add `BRIDGE_BIBLE_SVC_URL` to `.env.example`, to `_net_url("BIBLE", …)`
in `services/config.py`, to Identity's `settings_map`
(`bible_svc_url` → `BIBLE_SVC_URL`) and a Caddy route.

### Changes to existing code

The concrete edit list implied by the integration map. Nothing here is
optional plumbing — each row is a small, testable diff.

| File | Change | Test |
|---|---|---|
| `services/automation/main.py` | add `bible` to `DISPATCH_TARGETS` so timers can dispatch to the bible service (`path` must start with `/`, method in `ALLOWED_METHODS`, payload a dict — existing validation, no new rules) | `services/automation/tests/` — timer dispatch to `bible`, and rejection of a bad path/method (existing behaviour must not loosen) |
| `services/ui/src/components/settings/ActivitySharingPanel.tsx` | 4th scope entry `{id:'bible',...}` in the literal scope array; default stays `share: ['totals']` (private by default) | extend `src/test/ActivitySharingPanel.test.tsx` + `ActivitySharingPanelNonAdmin.test.tsx` |
| `services/ui/src/types/api.ts` | `bible` in the sharing-scope union | typecheck |
| `services/identity/models.py` | `DEFAULT_GLOBAL_SETTINGS` seeds `bible_svc_url`, `blb_base_url`, `bible_devotional_dir`, `bible_import_dir`, `bible_provider_cache`, `bible_provider_call_budget`, and `bible_api_key` (masked in the UI); and `UserActivitySharing` scope doc includes `bible` | identity settings test |
| `services/gateway/main.py` | `/api/bible/*` proxy block; **`/api/bible/speak`** proxy to execution `/execute/tts` (no generic TTS proxy exists today); share-card render proxy if card rasterization moves server-side | gateway proxy tests incl. upstream-error passthrough |
| `services/execution/handlers/note.py` | no change — but a new `category` value (`Bible`) is used by the reader so Bible notes land in `Bible/{Title}.md` and stay separable from other notes | existing notes tests |
| `services/geo/achievements.py` | no change — `reason` already accepts `achievement`/`game` | existing star tests |
| `services/ui/src/lib/chatEnvelope.ts` | new `card_kind` values (`bible_achievement`, `bible_plan_day`, `bible_verse`, `bible_quiz`) in the envelope type union; renderer already generic | envelope round-trip test |
| `services/storage/providers.py` | `StorageProvider.get_bytes(path)` on the ABC — `get_content` decodes bytes as text, so a PDF/EPUB comes back as replacement characters rather than an error. `None` means "this provider cannot give you bytes", which is a different answer from "the file was empty" | `test_a_provider_that_cannot_give_bytes_declines_instead_of_guessing` |
| `services/storage/providers_impl/nextcloud.py` | `get_bytes` → `NextCloudClient.get_file_bytes` (the binary-safe path; `get_file_content` mangles binaries) | `test_storage_provider_fetch.py` |
| `services/storage/providers_impl/calibre.py` | `get_bytes` returns `None` — the Calibre provider is an **index, not a file server**, so declining is the honest answer | `test_a_provider_that_cannot_give_bytes_declines_instead_of_guessing` |
| `services/storage/models.py` + `main.py` | `ProviderFetchRequest(provider, path, max_bytes)` and `POST /providers/fetch` returning `{status, path, name, size, content_b64}` behind `X-Internal-Secret`. 413 over the ceiling (both numbers named), 502 when `get_bytes` returns `None`, 422 when `max_bytes <= 0` | `test_storage_provider_fetch.py` (8 tests) |

Cross-reference: the shelf itself, and why it is indexed read-only, is
documented in [`docs/CALIBRE_LIBRARY.md`](CALIBRE_LIBRARY.md).

### Data model (`models.py`)

```
UserBibleState        username PK          last_read {book,chapter,verse}, last_open_day,
                                           font_scale, line_height, theme(serif|sans), default_version,
                                           read_aloud_voice, split_view(parallel|compare)
VerseMark             id, username, ref, kind(highlight|bookmark), color, note_path(nextcloud),
                                           note_preview, created_at, updated_at
Plan                  id, slug, title, description, days JSON[{day, refs[], title, note}], duration, source(builtin|family), owner
PlanProgress          username, plan_id, day, completed_at        (one row per completed day)
PlanGroup             id, plan_id, host, started_on, status       (family plan)
PlanGroupMember       group_id, username, role, joined_at
MemorySet             id, slug, title, refs JSON, builtin
MemoryVerseState      username, set_id, ref, level, due_at, correct, incorrect, last_result
QuizAttempt           id, username, mode, quiz_key, score, total, answered JSON, created_at
GameSession           id, username, mode, score, created_at
ReadingEvent          id, username, kind, ref, meta JSON, created_at   (kinds below)
BibleEdition         code(unique), version_code, name, publisher, language,
                                           license_class, rights_holder,
                                           note_count, note_kinds, imported_at
StudyNote            id, version_code, edition_code, osis, chapter,
                                           verse(0=chapter-wide),
                                           kind(commentary|footnote|introduction|heading),
                                           ordinal, body, source, imported_at
UserBibleState       username, last_book/chapter/verse, last_open_day, last_read_at,
                                           default_version, default_edition,
                                           favorite_version, compare_version,
                                           cross_version_notes, font_scale,
                                           line_height, theme, read_aloud_voice,
                                           split_view
```

`default_version` is where the reader is; `favorite_version` is the one they
come back to; `compare_version` is the one shown *beside* the text. Three
separate fields because they are three separate decisions — turning a
comparison off must not move the reader's own text. `compare_version` blank
means no comparison and is never guessed, and comparing a translation against
itself is refused, because the same words twice reads as a bug rather than as
a comparison. `split_view` chooses the *layout* of that second column
(`compare` = a row per verse, `parallel` = the whole second chapter).

Note bodies are **not** here. `VerseMark.note_path` points at a Nextcloud
file written by the existing notes handler; `note_preview` is a few words
for list rendering. The bible service stores an index, not a second copy.

`StudyNote` is the one place study material lives, and it is deliberately
**separate from the verse text**. `BibleVerse.text` is the scripture and
nothing else; commentary and footnotes are rows here, joined to a verse only
when the reader asks for them. `verse=0` means the note belongs to the whole
chapter (an introduction or a chapter heading), which is how the join knows to
show it alongside every verse rather than on one.

`StudyNote.edition_code` is part of a note's identity. Re-importing one study
Bible replaces only its own notes (`DELETE … WHERE version_code = ? AND
edition_code = ?`), so refreshing the Nelson commentary can never take the
MacArthur commentary with it. The two reader choices — **which study Bible**
and **notes from other translations** — are both resolved in one place,
`study._edition_filter()`, so there is exactly one answer to "which notes
apply here" no matter which endpoint asks.

Because `BibleEdition` and the two new `UserBibleState` columns are additive,
`services/bible/migrations.py` brings an existing corpus forward in place: it
adds the missing columns, adopts notes with no edition into one named after
their translation, and inserts a text-only edition for every translation that
had none. It reports what it did, is safe to run twice, and runs in the service
lifespan after `create_all` — so an existing `bible.db` upgrades on restart
with no re-import and no lost verses.

The note count and kinds on an edition row are a convenience so the picker never
has to group over tens of thousands of notes, which makes them a cache that can
drift — and a stale count reads to a reader as "this study Bible has no notes".
So they are treated as derived: `corpus.refresh_edition_notes()` recounts them
from the stored rows and returns only the editions it actually had to correct,
and `python3 -m services.bible.editions list` runs that before printing rather
than reporting a number it has not checked. `rename` refreshes its own row
because a rename moves the notes without rewriting the summary.

Redis keys (day-bucketed, TTL 400d, same discipline as geo):
`bible:read:{user}:{YYYY-MM-DD}` (chapter count),
`bible:open:{user}:{YYYY-MM-DD}` (app opens),
`bible:quiz:{user}:{YYYY-MM-DD}`, `bible:streak:{user}` (cached streak),
`bible:votd:{date}` (verse-of-day cache),
`bible:tts:{sha256(ref|version|voice)}` (cached WAV bytes from
`/execute/tts`, so re-listening a chapter is free and offline-tolerant).

**Reading events** (the "A LOT like the Bible App" tracking layer):
`app_open`, `chapter_read`, `chapter_complete`, `verse_tapped`, `search`,
`note_created`, `mark_created`, `plan_day_completed`, `plan_started`,
`quiz_played`, `quiz_correct`, `memory_recall`, `game_played`,
`assistant_ask`, `share_tap`, `blb_link_tap`, `tts_played`,
`tts_complete`. Note: events carry a *reference*, never note/highlight
*content* into any no-opt-in store — Identity's `FeatureUsage` is
metadata-only by design (`models.py` consent boundary), so Bible usage lives
in this service, visible to the user always and to the family only via the
opt-in scopes.

### Endpoints (service, proxied by the gateway under `/api/bible/*`)

| Endpoint | Purpose |
|---|---|
| `GET /books`, `GET /books/{book}/chapters/{ch}` | Reader text (vendored corpus; `?version=`) |
| `GET /passages?ref=John 3:16&version=` | Reference resolver (also used by Jarvis chips) |
| `GET /search?q=` | Full-text over corpus |
| `GET/PUT /marks` | Highlights/bookmarks/notes for the caller |
| `GET /state`, `PUT /state/position` | Last-read position, prefs |
| `GET /stats?window=today\|week\|month\|all` | Own usage totals |
| `GET /streaks` | App-open, reading, plan streaks |
| `GET /plans`, `GET /plans/{id}`, `POST /plans/{id}/start` | Plan catalog + start |
| `POST /plans/{id}/days/{day}/complete` | Complete a plan day (idempotent) |
| `GET /plans/{id}/progress` | Own progress + (family) group board |
| `POST /plans/{id}/groups` / `…/members` | Family plan host/invite (Identity usernames) |
| `GET /verse-of-day` | Deterministic-by-date verse + share card data |
| `GET /memorize/sets`, `POST /memorize/sets/{id}/recall` | Memory sets + record a recall result (advances SRS) |
| `GET /memorize/due` | Due queue |
| `GET /quizzes/{mode}` | Generate a quiz (templated; `?ref=` to scope) |
| `POST /quizzes/attempt` | Score an attempt (server re-verifies answers against corpus) |
| `GET /games/leaderboard?scope=family\|week` | Family leaderboard |
| `GET /achievements` | Earned + next-up with progress (`4/7 days`) |
| `GET /activity/summary?user=&window=` | Own, or a **consented** user's, scope-filtered totals (404 otherwise) |
| `GET /activity/feed?window=` | Opt-in activity of others (feed omits non-consented, never errors) |
| `GET /study/notes?ref=&version=&kind=&edition=&cross_version=` | Study apparatus for a passage (commentary, footnotes, introductions), fetched on demand. `edition` picks which study Bible; `cross_version` (default off) adds notes written for other translations. Returns every installed edition and every other translation that carries notes, so the client can render the choices |
| `GET /editions?version=` | Study Bibles installed for a translation, the default one, and which other translations carry notes |
| `GET /study/ask?ref=&q=` | Jarvis study answer (streaming via gateway LLM path; references post-processed to chips) |
| `GET /blb/link?ref=&tool=search\|lexicon\|concordance` | Pre-built blb.org deep URL (never proxies BLB content) |
| `GET /voices` | Narration voices from the execution TTS engine; an absent engine is a 503 naming `EXECUTION_SVC_URL` |
| `GET /narration?ref=&version=&voice=` | The passage as speech, cached per translation+reference+voice. Refuses rather than truncating: over 120 verses or 12,000 characters is a 400 naming the number |
| `GET /admin/imports` | **Admin.** Installed catalogue with `primary`/`provider`, declared providers with their configuration reason, supported kinds, the import directory, and the import history |
| `GET /admin/providers/{code}/translations` | **Admin.** What one provider can supply. Separate from the catalogue so listing it never needs the network |
| `GET /admin/library?path=` | **Admin.** One folder of the Calibre shelf: folders first, then files, each marked installable or carrying the reason it is not. An unreadable file is **shown with its reason**, not hidden — a shelf holding one unusable DOCX should not look like a shelf with a hole in it. Gateway: `POST /api/bible/admin/library` (POST, not GET: a WebDAV folder name carries slashes and can carry semicolons, so it is the wrong thing for a query string) |
| `GET /admin/providers/{code}/estimate?translation_id=` | **Admin.** What installing it would cost: chapters cached vs. remaining requests, the budget, and whether this run would be refused. Asked **before** the Install button is offered, not after |
| `POST /admin/imports` | **Admin.** Install from `source_path`, from `provider`/`provider_id`, or from `library_path`. 200 on success, **422 carrying the refusal** |
| `POST /admin/imports` with `library_path` | **Admin.** Fetch one file off the shelf and import it through exactly the same code an upload would use. The fetch log line comes first so the operator can see the bytes arrived before the refusal |
| `POST /admin/imports/upload` | **Admin.** Install a picked file. Extension checked before any write; 256 MB cap with the partial file removed |
| `POST /admin/imports` with `dry_run: true` | **Admin.** Fetch one book (Genesis 1), parse it, show the text and install nothing — one request instead of 1,189. The sampled chapter stays in the cache |

Cross-service rows (the bible service is the caller; these endpoints live
in the services named, and the gateway proxies them so the browser never
talks to an internal service directly):

| Called service | Endpoint | Purpose |
|---|---|---|
| execution (TTS) | `POST /execute/tts` via gateway `/api/bible/speak` | Read-aloud WAV for a chapter/ref; response cached in `bible:tts:*` |
| execution (TTS) | `GET /execute/tts/voices` | Voice picker in reader settings; empty/unavailable engine → visible state |
| execution (HA) | `POST /execute/ha_service` (`tts.piper`) | "Play on the kitchen speaker" while the family cooks |
| execution (notes) | `/execute/note` via gateway `/api/communication/notes/*` | Promote a highlight/Jarvis answer to a Nextcloud note (`Bible/{Title}.md`) |
| execution (chat) | gateway `POST /api/communication/talk/card` | Achievement / plan-day / verse / quiz cards into the family room |
| execution (games) | gateway `POST /api/communication/talk/game` | In-chat Bible trivia round |
| geo | `POST /api/geo/stars` | Bank every award and score into the one ledger |
| identity | `GET /api/internal/activity-sharing` | Consent check before any cross-user read |
| rag | `POST /rag/ingest`, `POST /rag/search` | Study memory: promote a note to searchable memory; "where did we discuss this?" |
| automation | timer create/update (gateway `/api/communication/timers`) | Reminder schedule per user, timezone-aware |
| telemetry | `send_to_user` / outbox `tel:notify:{user}` | Reminder and achievement delivery to the phone |
| workspace_runtime | `/files/write`, `/workflow/write-sync-commit` | Opt-in export of journal / memory sets to a family workspace |
| logging | structured logger | Every award, rejection and upstream failure |

Gateway proxies each route following the `/api/geo/achievements` pattern:
resolve caller (`_acting_identity`), call upstream with
`X-Internal-Secret`, pass JSON through, raise with upstream detail on
failure.

### Achievements (data-driven, derived)

`services/bible/bible_achievements.json` — same `kind`/`rule` schema as
geo's:

```json
{
  "schemaVersion": 1,
  "kind": "jarvis.bible.achievements",
  "achievements": [
    { "id": "first_chapter",  "name": "First Chapter",    "points": 1,  "rule": {"type": "chapters_total",  "value": 1} },
    { "id": "read_10",        "name": "Ten Chapters",     "points": 5,  "rule": {"type": "chapters_total",  "value": 10} },
    { "id": "read_streak_7",  "name": "Week of Reading",  "points": 15, "rule": {"type": "read_streak",     "value": 7} },
    { "id": "read_streak_30", "name": "Month in the Word","points": 60, "rule": {"type": "read_streak",     "value": 30} },
    { "id": "plan_first",     "name": "Plan Starter",     "points": 5,  "rule": {"type": "plan_days_total", "value": 1} },
    { "id": "plan_complete",  "name": "Plan Finisher",    "points": 30, "rule": {"type": "plan_completed",  "value": 1} },
    { "id": "memory_1",       "name": "Hidden in Heart",  "points": 10, "rule": {"type": "memory_mastered", "value": 1} },
    { "id": "quiz_perfect",   "name": "Perfect Score",    "points": 10, "rule": {"type": "quiz_perfect",    "value": 1} },
    { "id": "quiz_25",        "name": "Quiz Kid",         "points": 25, "rule": {"type": "quizzes_total",   "value": 25} },
    { "id": "whole_bible",    "name": "Cover to Cover",   "points": 200,"rule": {"type": "books_read",      "value": 66} }
  ]
}
```

Rules computed from the Redis day-buckets + SQLite tables; each unlock
banks points into `geo:points:{user}` **first**, then posts an activity
card to the family Talk room via the existing typed envelope
(`achievements.announce_awards` pattern — silent unless
`FAMILY_CHAT_TOKEN` names a room, so a deployment without family chat
never posts). Awards are banked on first read so a badge keeps its
first-earned date.

### Family sharing

- `UserActivitySharing.data.share` gains the `"bible"` scope
  (alongside `totals`, `workouts`, `achievements`); the existing
  audience/`user_ids` enforcement in geo is reused verbatim — the bible
  service calls Identity `GET /api/internal/activity-sharing` before any
  cross-user read.
- Shared slices when `bible` is opted in: reading totals, current plan +
  progress, streaks, earned badges, mastered memory verses. **Never**
  shared: note/highlight content, quiz answers, Jarvis questions.
- Family plans: `PlanGroup` rows; the group board endpoint projects only
  members' day progress (no reading content). Host invites from the
  Identity user list; invite state visible in the Talk room as a card.

### Quizzes, memorization, games

**Quiz generators** (`services/bible/quiz_generators.py`, data-driven):
`trivia` (MCQ — who/what/where), `fill_blank` (cloze with the word
removed, 4 options), `verse_hunt` ("which book contains…"), `book_order`
(sequence), `true_false`. Deterministic selection seeded by
`(user, mode, date)` so the daily quiz gives every family member the same
set → fair leaderboard. Jarvis may *author* extra questions for a
passage, but the server validates every generated question against the
corpus (answer string must appear in the referenced text) and drops the
batch with a visible error if validation fails.

**Memorization** (`services/bible/memorize.py`): recall ladder
`read → cloze → first-letters → full recall`; each verse has
`level` (0–4) and SRS-lite `due_at` on an interval ladder
(1d → 3d → 7d → 14d → 30d → 90d); a wrong recall drops a level and
reschedules. Challenge modes: **timed recall**, **chain** (recite the
next verse in a set in order), **weekly set** (Sunday review of the
week's due verses).

**Games**: `daily_quiz` (date-keyed, leaderboard), `verse_race` (answer
as fast as possible, streak bonus), `guess_the_verse` (Jarvis paraphrases
a verse, you name it — paraphrase verified against corpus), `word_find`
(generated from a passage's vocabulary). Scores bank to the points ledger
(`reason: "game"`, mirroring `family_games.py`'s star banking) so a geo
outage is logged and never breaks a game in progress.

### Jarvis study assist

- **Ask panel**: every Reader/Study surface has "Ask Jarvis about this
  passage" → `GET /api/bible/study/ask?ref=&q=` (streaming). System
  prompt constrains answers to the passage + cross-references, with three
  explain-depths (child / teen / adult) selectable in UI. Each question
  logs `assistant_ask`.
- **Reference chips**: answers (and notes, and Jarvis chat anywhere) are
  post-processed by a scripture-reference regex → tappable chips; tap
  opens the reader at that ref; hover/tap-hold shows a popover with the
  verse text (our ScriptTagger analog). Every chip carries a "Study at
  Blue Letter Bible" link (deep URL from `/blb/link`).
- **Intent engine**: route "read john 3", "quiz me on the beatitudes",
  "what does this verse mean", "test me on my memory verses" to the
  matching bible feature (existing sentence-transformer intent flow).
- **Study aids Jarvis can generate** (server-validated): cross-reference
  lists, discussion questions for a plan day, a quiz from any passage,
  memory-verse suggestions from reading history, "explain like I'm 5".
- **Fail-fast**: no LLM provider configured → the ask panel shows the
  standard unconfigured-assistant message (same as everywhere else in the
  app), never a canned answer.

## UI / UX architecture

### Information architecture

New route **`/bible`** (`src/pages/Bible.tsx`), registered in
`App.tsx` `ProtectedRoute`, nav entry **Bible** in
`components/layout/Sidebar.tsx` (desktop), `BottomNav.tsx` (mobile),
and the Header command-palette search index. API methods in
`services/api.ts`, types in `types/api.ts`.

Inside the page: **section tabs** —
**Read · Plans · Study · Memorize · Play · Progress** —
a segmented control on phones (sticky under the safe-area header,
thumb-sized targets) and a left rail on desktop. All sections are
components under `src/components/bible/`.

```
┌─────────────────────────── phone (≤768px) ───────────────────────────┐
│  ← Bible                        ⌘ search        streak 🔥 12        │
│ ┌───────────────────────────────────────────────────────────────────┐ │
│ │ [Read][Plans][Study][Memorize][Play][Progress]   ← segmented tab │ │
│ ├───────────────────────────────────────────────────────────────────┤ │
│ │  section body (single column, 44px targets, safe-area bottom)     │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│  BottomNav: Home · Wander · Family · Bible · Health · …              │
└────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── desktop ──────────────────────────────────┐
│ Sidebar …  │  Bible                                    streak 🔥 12  │
│  Read      │  ┌─ section toolbar ──────────────────────────────────┐ │
│  Plans     │  │  version ▾  font Aa  compare  Ask Jarvis          │ │
│  Study     │  ├────────────────────────────────────────────────────┤ │
│  Memorize  │  │  two-column: reader/study + side panel            │ │
│  Play      │  │  (marks, cross-refs, Jarvis thread, stats)        │ │
│  Progress  │  └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

### Reading parity with the Bible App (Phase 1 acceptance checklist)

This is the definition of done for the reader. Every row is a checkbox on
Phase 1, not a nice-to-have. If a row can't be shipped, the row moves out
of Phase 1 explicitly rather than being quietly dropped.

| Must-have (Bible App parity) | How we meet it | Phase |
|---|---|---|
| **Opens where you left off** — cold start lands on the last verse you read | `last_read {book,chapter,verse}` in `UserBibleState`; `/state/position` saved on debounced scroll; "Continue reading" is the Read landing card and the default route for `/bible` | 1 |
| **Text you can actually read** — font size, line height, serif/sans, dark & light | `font_scale`, `line_height`, `theme` persisted per user; typographic scale tuned for long-form (≈65ch measure, 1.6 leading, verse numbers de-emphasized) | 1 |
| **Tap anything** — verses are targets, not text | Verse chips ≥44px row hit area on `pointer-coarse`; whole verse row tappable; tap-hold opens the action sheet on touch (no hover-only path) | 1 |
| **Highlights + bookmarks in color** | `VerseMark` with 5–8 colors, per-chapter color rail, "highlights in this chapter" strip | 1 |
| **Notes on a verse** | Inline note editor; body written to **Nextcloud via the notes handler** (`Bible/{Title}.md`), index row only in the bible service | 1 |
| **Marks sync across devices** | Marks live in the bible service (server-side), not local storage, so phone + desktop agree; cached for offline | 1 |
| **Chapter/book navigation** | Book grid (66 books), chapter grid, "next/previous chapter" swipe + buttons, "go to date" for plans | 1 |
| **Search that finds verses** | Local corpus search (word, phrase, reference, `"John 3:16"`, ranges `John 3:16-21`, multi-passage `John 3:16, 4:22`); BLB `preSearch` deep link for the long tail | 1 |
| **Read aloud** | `POST /execute/tts` via `/api/bible/speak`; per-verse highlight while speaking; play/pause, speed, voice picker; cache in `bible:tts:*`; "play on the kitchen speaker" via `/execute/ha_service` `tts.piper` | 1 |
| **Verse of the Day** | Deterministic by date, cached `bible:votd:{date}`, at the top of Read and in the `bible_daily` widget | 1 |
| **Share a verse** | Verse Image card (SVG, theme-aware, scales with container) → native share sheet or web share; deep link back to the passage | 1 |
| **Compare / parallel versions** | `compare_version` picks a second translation, fetched from `/passages` like any other. `ChapterReader` puts the two in one row per verse from `sm` up and stacks them on a phone, since a phone cannot show two readable columns. A verse the second translation does not have says "Not in this translation." rather than vanishing — NIV2011 genuinely omits Matthew 17:21, and an invisible gap reads as a rendering fault. The version picker never offers the translation being read, and the server refuses it too | 1 |
| **Streak on screen** | App-open streak (offline-tolerant) and Guided-Scripture streak (resets on a skipped day, number hidden until earned again) — YouVersion's two-streak model, not one generic counter | 1 |
| **No network? Still read** | Corpus is local; last chapter + bookmarks + VOTD + plan day cached in TanStack query + Capacitor Preferences; reads never wait on an upstream | 1 |
| Offline audio download | Recorded audio is out of scope (licensed); cached TTS WAVs give repeat-listen without network. Documented as a deliberate gap, not a bug | later |

Deliberately **not** in Phase 1 (and why): 1,200+ versions (needs
licensed API keys — the picker shows PD versions offline and unlocks more
only when `bible_api_key` is configured), recorded audio (licensed), verse
images with photo backgrounds (camera-roll import is a later polish item).

### Screen specs

**1. Reader** (`components/bible/ChapterReader.tsx`) — *the product*
- **Landing**: "Continue reading" card (book, chapter, verse, streak chip)
  is the default state of `/bible`; above it the Verse of the Day, below it
  "3 verses due" and today's plan card only if a plan is active. Everything
  else lives in the other tabs.
- **The text**: verse numbers as tappable chips (≥44px row hit area on
  `pointer-coarse`); whole verse row tappable; red-letter and words-of-Jesus
  styling when the corpus supports it; "Show passage" title header with the
  book name and chapter; a thin progress rail showing position in the
  chapter; the chapter's bookmarks/highlights summarized in a strip under
  the header.
- **Toolbar** (always one tap away, never a modal maze): version selector,
  font size, line height, serif/sans, **Compare**, chapter picker,
  **read aloud** (speaker), search, "Ask Jarvis" (which scrolls to the
  Study tab with the current passage pre-loaded rather than opening a
  second app).
- **Verse action sheet** (tap / tap-hold): **Highlight** (colors),
  **Bookmark**, **Note** (inline editor → Nextcloud), **Copy**,
  **Share** (native sheet via Capacitor → Verse Image card),
  **Study notes**, **Study at BLB** (deep link), **Add to memory set**,
  **Add to plan day**.
- **Study notes** (`components/bible/BibleStudyNotes.tsx`) — the sheet's
  "Study notes" row opens a panel that asks `/study/notes` for *that verse*
  in *that translation*. Verses carrying material are marked in the reader
  with a small bookmark glyph (and announced to screen readers as "Has
  study notes") so a study Bible announces itself without ever mixing a
  commentary into the text. The panel offers one filter per kind the
  installed translation actually carries, with counts
  (`Commentary · 12055`), and hiding a kind is a toggle rather than a
  reload. Chapter-level material (introductions, headings) is shown once
  above the verse notes. When the translation carries none — KJV, ASV,
  WEB — the panel says so in one sentence and names
  `python -m services.bible.import_epub --import-notes`, rather than
  showing an empty box. It names the study Bible it actually loaded, and
  offers the two choices the reader owns:

  - **Study Bible** — chips appear only when more than one edition is
    installed for the translation, each labelled with its note count
    (`The MacArthur Study Bible · 9,800`). Tapping one refetches, and the
    choice is saved as `default_edition` so the next device opens the same
    commentary. The toolbar carries the same picker next to the translation,
    because that is where someone looks for it.
  - **Notes from other translations** — an unchecked checkbox naming the
    study Bibles it *would* add ("NKJV, Life Application Study Bible,
    New International Version"). Once on, every note carries a
    translation + study Bible badge and notes group by study Bible before by
    verse, so a reader comparing two commentaries reads one at a time
    instead of alternating per verse.

  Neither choice is a server opinion: the server resolves them and reports
  what it resolved (`edition`, `edition_name`), and the reader's choice is
  what gets remembered.
- **Version selector**: the corpus catalogue first — a translation that is
  catalogued but not installed appears greyed with the reason and the
  command that installs it; api.bible versions appear only when
  `bible_api_key` is configured — otherwise a visible "configure
  bible_api_key to unlock more versions" item (fail-fast, never a silent
  3-version ceiling). If a saved translation is not installed, reading
  continues in the best installed one *and* a banner says which was
  missing.
- **Read aloud**: chapter or verse range narrated; spoken verse highlighted
  in sync; play/pause, 0.75×–1.5× speed, voice picker from
  `/execute/tts/voices`, `read_aloud_voice` persisted; cache hit is instant
  and works offline; speaker route via HA `tts.piper` when the user asks for
  the kitchen speaker. **If no voice engine/voices are installed** the
  button is present but the panel states exactly why
  (`FileNotFoundError: Kokoro voices missing`) and how to fix it — never a
  button that does nothing.
- **Compare**: side-by-side versions with synchronized scroll on desktop,
  stacked tabs on phone, per-pane font control, and a "diff" highlight for
  verses that differ textually.
- **Reading position** saved on debounced scroll → `PUT /state/position`;
  `chapter_read` + `chapter_complete` events emitted server-side so the
  streak and stats are not client-reported.

**2. Plans** (`components/bible/Plans.tsx`, `PlanCard.tsx`, `PlanDetail.tsx`)
- Catalog: cards with title, duration, description, sample day, source
  badge (Built-in / Family); "Save for later" shelf.
- Detail: day checklist with checkmarks, progress bar, current-day card,
  streak flame, **Catch Me Up** (marks missed days complete in one tap —
  YouVersion parity), reminders toggle (automation job).
- **Family plans**: "Start with family" → pick plan + start date +
  members (Identity users) → group board: one row per member, 7/30-day
  dot grid, host highlighted; per-day discussion posts into the family
  Talk room as typed cards (reuses `post_card` / envelope).
- Member progress is visible to group members only; non-members get 404.

**3. Study** (`components/bible/StudyPanel.tsx`)
- Passage pane (any ref, parallel versions, cross-references list).
- **Jarvis thread** scoped to the passage (`ChatPanel`-style bubbles,
  streaming, reference chips inline, depth selector child/teen/adult,
  "Generate quiz", "Suggest memory verse", "Discussion questions").
- **BLB tools row**: Strong's/lexicon/concordance/dictionary buttons →
  blb.org deep links (new tab on desktop, InAppBrowser on native).
- Notes for the passage listed under the text; note editor with the
  reference chip composer.

**4. Memorize** (`components/bible/Memorize.tsx`, `RecallFlow.tsx`)
- Sets list (built-in packs as data JSON + user-created sets), per-set
  stats (mastered/total, due count, level dots).
- **Recall flow** (full-screen modal, haptics on each step):
  `read → cloze → first-letters → full recall`, self-grade
  "Got it / Almost / No" driving the SRS ladder; correct streak triggers
  `useHaptics().trigger()` and a level-up animation.
- **Challenges**: Timed recall (countdown, score), Verse chain (next
  verse in order), Weekly review (Sunday card with the week's due
  verses).
- Due queue card on the section landing ("3 verses due today").

**5. Play** (`components/bible/Play.tsx`, `QuizEngine.tsx`)
- Hub cards: **Daily Quiz** (date-keyed, family leaderboard), **Trivia**,
  **Fill in the Blank**, **Verse Hunt**, **Book Race**, **Guess the
  Verse**, **Word Find**.
- `QuizEngine`: generic question runtime (MCQ/cloze/sequence/true-false
  renderers), 4 large option buttons, progress dots, timer chip where
  relevant, haptic on correct, gentle shake on wrong (native), result
  sheet with score, points earned, "Ask Jarvis why" deep-link into the
  Study panel.
- Leaderboard (week/family) with per-member avatars; scores banked to the
  shared points ledger.

**6. Progress** (`components/bible/Progress.tsx`)
- Stat tiles (today / week / month / all): chapters, verses, searches,
  quizzes, memory recalls — recharts area chart (desktop) / compact bars
  (phone).
- Streak ring set (app-open, reading, plan) with "don't break the chain"
  reminder state.
- **Achievements row** — reuse the `AchievementsPanel` patterns
  (`components/wander/AchievementsPanel.tsx`): points balance, earned
  badge grid, next-up with `4/7 days` progress.
- **Sharing card** — extend `ActivitySharingPanel` with the `bible`
  scope: audience picker (circle / specific users), scope checkboxes.
- "Share verse" composer → Verse Image card (theme-aware, SVG text
  rendering, no fixed-pixel canvas per AGENTS.md) → native share.

### Dashboard widget: `bible_daily`

Registered in `stores/widgetStore.ts` (`WidgetKey` += `'bible_daily'`,
`defaultWidgetDefs`, `defaultSizes`, `LazyWidgets`), component
`components/widgets/BibleDailyWidget.tsx`, sizes small→tall:
- small: VOTD reference + "tap to read".
- medium: VOTD text (clamped) + streak chip.
- tall: VOTD + streak + next due memory verse + plan progress bar.
- Capability gate `has_bible_config` (true when corpus is loaded — it
  always is; the gate exists so an unconfigured upstream still shows the
  honest state). Respects `UserThemeSetting`; SVG `viewBox` + percentage
  sizing; haptic on tap; tap opens `/bible`.

### Mobile parity checklist (AGENTS.md, applied)

- 44px touch targets: interactive rows, option buttons, steppers; desktop
  density kept via `pointer-coarse:min-h-11`.
- No hover-only affordances; no `onMouseEnter`-only paths (reference
  popover opens on tap-hold on touch).
- Phone grid fit: widget content scales with container (SVG `viewBox`,
  no fixed pixel canvas); page sections fit 280px/200px bands.
- `safe-area-*` insets on sticky bars and composers; `text-base` inputs
  (no iOS zoom); single-column stacking on phones.
- Native branches via `Capacitor.isNativePlatform()`: share sheet,
  InAppBrowser for BLB links, haptics gating; `useHaptics()` on streak,
  correct answer, level-up, achievement unlock.
- Offline tolerance: last chapter + VOTD + due queue in TanStack cache
  and Capacitor Preferences; app-open streak counts offline and syncs on
  reconnect (YouVersion parity).
- Themed (light/dark + theme packs), serif option for Scripture.

### Component inventory (new files)

```
src/pages/Bible.tsx
src/components/bible/BibleSectionNav.tsx        # segmented tabs / left rail
src/components/bible/ChapterReader.tsx
src/components/bible/VerseActionSheet.tsx
src/components/bible/VerseChip.tsx              # reference chip + popover (ScriptTagger analog)
src/components/bible/BookPicker.tsx             # 66-book grid + chapter grid
src/components/bible/ReaderToolbar.tsx          # version / font / compare / aloud / ask
src/components/bible/TypographyControls.tsx      # font scale, line height, serif/sans
src/components/bible/BibleReadAloud.tsx          # narration player + voice picker
src/components/bible/ReferenceBar.tsx            # ref input, book/chapter steppers,
                                                #   version, study Bible, compare, display
src/components/bible/BibleToday.tsx              # verse of the day + today's devotional
src/components/bible/MarksRail.tsx              # highlights/bookmarks strip per chapter
src/components/bible/NoteEditor.tsx             # verse note -> Nextcloud via notes API
src/components/bible/ReferencePopover.tsx       # tap-hold tooltip (7-verse cap + "More »")
src/components/bible/ContinueReadingCard.tsx
src/components/bible/VerseOfDayCard.tsx
src/components/bible/VersionPicker.tsx
src/components/bible/CompareView.tsx
src/components/bible/ScriptureSearch.tsx
src/components/bible/Plans.tsx
src/components/bible/PlanCard.tsx
src/components/bible/PlanDetail.tsx
src/components/bible/FamilyPlanBoard.tsx
src/components/bible/StudyPanel.tsx
src/components/bible/BibleStudyNotes.tsx           # commentary/footnotes per verse,
                                                    # study-Bible picker + cross-version
src/components/bible/JarvisStudyThread.tsx
src/components/bible/Memorize.tsx
src/components/bible/RecallFlow.tsx
src/components/bible/Play.tsx
src/components/bible/QuizEngine.tsx
src/components/bible/Leaderboard.tsx
src/components/bible/Progress.tsx
src/components/bible/VerseImageCard.tsx         # share card
src/components/bible/StudyMemorySearch.tsx      # "where did we discuss this?" (RAG)
src/components/bible/WorkspaceExportPrompt.tsx  # opt-in journal export (workspace_runtime)
src/components/widgets/BibleDailyWidget.tsx
src/components/widgets/skeletons/BibleDailySkeleton.tsx
src/components/admin/BibleAdminPanel.tsx         # Admin › Bible: catalogue,
                                                #   providers, upload/path import,
                                                #   import history, cost estimate
                                                #   before Install, one-book test
```

Tests: `src/pages/Bible.test.tsx` (including comparing two translations: the
picker never offers the translation being read, the second column renders, a
verse the second translation lacks says so, and turning the comparison off
leaves the reader's own text alone),
`src/components/bible/BibleStudyNotes.test.tsx`,
`services/bible/tests/test_bible_editions.py` (two study Bibles over one
translation, cross-version gating, re-import scoping, recounting a stale note
cache),
`src/components/widgets/BibleDailyWidget.test.tsx` (vitest), mobile-layout
test following `src/test/adminMobile.test.tsx` (mock `useHaptics`, assert narrow
rendering), service tests `services/bible/tests/` (rules engine,
reference resolver, PDF and EPUB extraction, study-note joining, quiz
validation, SRS ladder, sharing enforcement, fail-fast unconfigured upstream),
gateway proxy tests.

Read-aloud: `src/components/bible/BibleReadAloud.test.tsx` plays the returned
bytes through a `data:`/blob URL and asserts the visible "no voices installed"
and "passage too long" states rather than a silent no-op.

Imports: `src/components/admin/BibleAdminPanel.test.tsx` covers the catalogue
with its primary marker, an unconfigured provider naming `bible_api_key`,
installing from a provider, from a server path and from an upload, a refusal
shown in full, and the retry when the catalogue cannot be read — plus the cost
question asked before anything is spent, a fully cached translation costing zero, an
over-budget run disabling Install, and the one-book dry run.
`services/bible/tests/test_bible_import.py` covers the importer and the admin
routes; `services/bible/tests/test_bible_provider_cache.py` covers write-once
storage (atomic writes, corrupt entries missed rather than served, dialect and host
changes getting a fresh directory, a key rotation keeping the cache), a budget that
is set but unreadable being an error rather than an absent ceiling, an operator
being able to reach every bible setting from the config database (and `bible_api_key`
deliberately *not* needing a restart), and an unreadable budget refusing the import
instead of running it;
`services/gateway/tests/test_bible_proxy.py` pins that imports and estimates are
admin-only, that an over-budget estimate still reaches the UI so it can warn, and
that the upload is re-multiparted with its file. `scripts/merge_env.py` is exercised
by a scratch harness covering a fresh host, a preserved host value, an `export`
line, a host file with no trailing newline and a byte-identical no-op.

## Configuration matrix (fail-fast)

| Setting | Where | Behavior when unset |
|---|---|---|
| `BRIDGE_BIBLE_SVC_URL` | `.env` → `_net_url("BIBLE")` | Gateway proxy returns clear 502 naming the setting |
| `bible_svc_url` | Identity `GlobalSetting` (runtime override) | same as above |
| `BIBLE_DATABASE_URL` | `.env` → SQLite file under `/data` | Every CLI and the service refuse to start with a message naming the setting (no default path in code) |
| `BIBLE_DEVOTIONAL_DIR` | `.env` / `bible_devotional_dir` `GlobalSetting` | Local devotional source is skipped **with a named reason**; the day still resolves through the next source, and `/devotional` reports every source that was skipped and why |
| `bible_api_key` (`BIBLE_API_KEY`) | Identity `GlobalSetting` / env | Enter it in Admin › global settings (the field is masked because the key contains `api_key`) or set `BIBLE_API_KEY`. The bible service reads it **from Identity at call time**, so a key saved in the app works without a restart; a blank setting never erases one the environment supplied. The api.bible provider is still **listed** on Admin › Bible with its reason naming this setting rather than hidden |
| `BIBLE_IMPORT_DIR` (`bible_import_dir`) | `.env` / `GlobalSetting` | Admin › Bible says nothing can be installed yet and why; reading is unaffected. A file import refuses with that sentence rather than picking a path |
| `BIBLE_PROVIDER_CACHE` (`bible_provider_cache`) | `.env` / `GlobalSetting` | Where fetched provider chapters are kept write-once. Blank derives `<directory of BIBLE_DATABASE_URL>/provider-cache`. Left unset, every chapter is fetched again on the next import — Admin › Bible says so on the cost line rather than importing quietly uncached |
| `BIBLE_PROVIDER_CALL_BUDGET` (`bible_provider_call_budget`) | `.env` / `GlobalSetting` | The most **new** requests one provider import may need. Blank means no ceiling. A run over it is refused before the first request, naming both numbers; only uncached chapters count, so a warm cache costs nothing |
| `CALIBRE_LIBRARY_PATH` (`calibre_library_path`) | `.env` / `GlobalSetting` — **the same setting the storage service's Calibre RAG index uses**, so a server already indexing books gets Bible imports free | Unset, Admin › Bible's Browse button is disabled and says so by name. **Not** a guess: a guessed shelf reports the wrong folder as an empty library, which is worse than saying nothing. The bible service reads it from Identity **per call**, so pointing it at a shelf takes effect without a restart (the storage index reads it through `resolve_runtime_config()`, which needs one) |
| `STORAGE_SVC_URL` | `.env` (compose sets it from `BRIDGE_STORAGE_SVC_URL`) | The shelf browse and fetch routes return "storage_svc_url is not configured", naming the setting — the bible service never holds Nextcloud credentials itself |
| `blb_base_url` | Identity `GlobalSetting` (default `https://www.blueletterbible.org` is a **documented default in settings seed**, not a code fallback) | BLB tool buttons return the server's "blb_base_url is not configured" message in the action sheet |
| TTS voices (`/execute/tts/voices`) | existing execution service (`DEFAULT_TTS_VOICE`, Kokoro/Edge) | Reader states the real engine error (e.g. `Kokoro voices missing`) and how to install; never plays nothing |
| LLM provider (`assistant_model` etc.) | existing config | Ask Jarvis shows the standard unconfigured message |
| `FAMILY_CHAT_TOKEN` | existing | Achievement/plan cards stay silent (existing pattern) |
| Notes storage (`nextcloud` \| `local`) | existing notes config | Falls back to `LOCAL_NOTES_ROOT` (existing behavior) — the reader shows which store it wrote to |
| Workspace opt-in | `config/workspaces.json` (existing) | Export button absent + a line saying no study workspace is enabled |

### `.env` is a seed; the config DB is the runtime

`.env` is read **once**, at startup, and only ever seeds. After that the
Identity `GlobalSetting` table is the runtime source of truth, editable from
Admin › Settings — no restart, no editing files on the host.

The rule already lives in `services/config.py::resolve_runtime_config()`: it
applies a database value **only when that value is truthy**, so `.env` fills
the gaps and the database overrides wherever an operator has actually set
something. Every bible setting above is therefore listed as
`.env` seed → `GlobalSetting` runtime, and `bible_provider_cache`,
`bible_provider_call_budget` and `bible_import_dir` were seeded into
`DEFAULT_GLOBAL_SETTINGS` precisely so they are reachable from the UI rather
than only by editing a file on the host.

Two deliberate exceptions, both because a stale value would be worse than a
re-read:

| Setting | Why it is not boot-time |
|---|---|
| `bible_api_key` | Read from Identity **per call** by `main._live_settings()`. A key pasted into Admin takes effect immediately instead of needing a restart — and rotating a revoked key takes effect just as fast. Blank saved never erases an env-supplied key. |
| `BIBLE_PROVIDER_CALL_BUDGET` | Coerced with `int()` at the point of use. A malformed value (`12oo`, `0`, `-5`) is an **error naming the setting**, not a silently absent ceiling. |

### Getting `.env` onto a new host

`scripts/deploy_remote.sh` pipes the local `.env` over ssh into
`scripts/merge_env.py`, which **appends only what the host is missing**:

- a key already defined on the host is **preserved**, never rewritten;
- the host's file is copied to `.env.bak` first — but only when one existed,
  since a fresh host has nothing to back up;
- a local file that defines a key twice uses the first definition and reports
  the duplicate rather than silently picking one;
- `export KEY=value` lines are carried across as well as bare assignments;
- nothing at all on stdin is a hard failure, so the deploy aborts rather than
  reporting success it did not achieve.

The two halves of that are deliberate: **`.env` seeds the host**, the
**config DB is managed through the UI** from then on.

## Licensing & legal

- **blb.org**: deep links and their published web-tool embeds only. Their
  lexicon/commentary/site text is copyrighted — never scraped or
  reproduced in-app. Attribute "Blue Letter Bible" on study link-outs.
- **Vendored corpus** (KJV/ASV/WEB): US public domain; include the
  standard attribution in Settings → About.
- **Family-owned licensed files** (NKJV, and ESV/NIV/NLT if supplied):
  the publisher's text stays inside the household. `corpus_manifest.json`
  records the rights holder and `license_class: "licensed"`; the
  catalogue marks those rows do-not-redistribute, `data/bible/` is
  gitignored, and no licensed verse text is ever written into a
  shareable artefact, a chat card, or an achievement announcement.
- **Study notes** inherit the translation's licence. They are stored
  beside the text and served only to signed-in family members through the
  gateway; they are never included in activity summaries, stats or the
  family feed, which carry counts and references only.
- **api.bible**: only Creative Commons / public-domain bibles for the
  non-commercial tier; per-translation license check before adding any
  translation; record permitted translations in the service config. Not
  on the default path — the documented `/v1/*` routes returned 404 when
  probed, and depending on an unverifiable contract is not depending on
  anything.
- All share cards render our own SVG text (no third-party artwork).

## Phased implementation

Reading is the gate. Phase 1 is not "a slice of the Bible feature", it is
the reader, and it is not declared done until every row of §"Reading
parity with the Bible App" passes on desktop **and** phone.

| Phase | Scope | Definition of done |
|---|---|---|
| **0** Config + corpus + reading + study ✅ | `services/bible/` service (port 8010), compose + Caddy + `BRIDGE_BIBLE_SVC_URL` + Identity `GlobalSetting`s, reference resolver, `corpus_manifest.json`, PDF + EPUB importers, `StudyNote` table with the two-axis `BibleEdition` corpus, `migrations.py` for existing databases, `editions.py` CLI, gateway `/api/bible/*`, `/books`, `/versions`, `/passages`, `/search`, `/editions`, `/study/notes`, `/verse-of-day`, `/devotional`, `/marks`, `/state`, `/events`, `/stats`, `/streaks`, `/achievements`, `/activity/*`, `/blb/link`, `/daily`, `/narration`, `/voices`, `/admin/imports*`; `/bible` page (Read/Today/Progress), `bible_daily` dashboard widget, Android Verse-of-the-Day home-screen widget, `bible` sharing scope, Admin › Bible import surface, one primary translation, long-term per-chapter provider cache with cost estimate, request budget and one-book dry run; `bible` added to the image CI matrix | **Shipped.** Service boots in compose; corpus imported (KJV/ASV/WEB + NKJV with 44k study notes under the Nelson study Bible); multiple study Bibles per translation and opt-in notes from other translations both work end to end; every unconfigured path returns a message naming the setting; proxy is caller-scoped with the family 404 intact |
| **1** **The reader** (full parity checklist) | `BookPicker`, `ReaderToolbar`, `TypographyControls`, `CompareView`, HA speaker playback (read-aloud itself ships in Phase 0 via `BibleReadAloud.tsx`), `NoteEditor` → Nextcloud, `ContinueReadingCard`, `VerseImageCard` share, `ReferencePopover`, offline cache | A user opens the app and is reading, where they left off, on desktop and phone; marks/notes/position sync; chapters read offline; read-aloud speaks a chapter and states its errors honestly |
| **2** Plans + daily rhythm | Plan model + built-in catalog, day completion, "catch me up", per-day reminders (automation + telemetry), plan-dashboard widget | Completing a plan day advances the plan, the streak, and fires the reminder; missed days are catchable |
| **3** Achievements + points | Rules engine already shipped in Phase 0 (reading/streak rules); plan, memory and quiz rules land with those features; banking to `POST /api/geo/stars`, Talk announcement cards, `AchievementsPanel` reuse in Progress | Unlock → points land in the one ledger, `earned_on` is stable across re-reads, family card posts when configured |
| **4** Family sharing | `PlanGroup` + family plan board, per-day discussion cards in Talk, in-chat Bible trivia (`services/games/bible_trivia.json`); the `bible` sharing scope already ships in Phase 0 | Non-consented user gets 404; feed omits; family plan board and Talk round work for members only |
| **5** Memorization | Memory sets, recall flow, SRS ladder, challenges, due queue | Recall session advances levels; due queue respects intervals; due count drives a widget line |
| **6** Quizzes + games | Generators, `QuizEngine`, daily quiz, leaderboard, Jarvis-authored (validated) questions, family leaderboard in cards | Daily quiz identical per family member; server rejects a bad generated batch (test); scores bank to geo |
| **7** Jarvis study | Study panel, streaming ask, reference chips, intent routing, study-aid generation, **study memory** (RAG ingest with citable ids + `StudyMemorySearch`), **workspace export** (opt-in), 7pm nudge | Ask a question on a passage → streaming answer with chips; "where did we discuss this?" returns prior family notes with citations; export writes to the workspace when enabled |
| **8** Polish | Verse images with camera-roll backgrounds, widget sizing pass, Android shortcuts, offline audio cache warming, quiet hours | Native share sheet verified; quiet hours honored |

Each phase: co-located service tests + UI vitest + mobile layout check
before "done" (AGENTS.md). Phases 0–1 ship the reading product (this is
what makes it a Bible *reader*); 2–4 the tracking/achievement/family
layer that makes it "A LOT like the Bible App"; 5–6 the games; 7 the
Jarvis learning layer; 8 polish. Phases 2–8 are optional to the reader —
each must be additive and must never regress Phase 1 (regression tests in
Phase 1 guard this).

## Risks / open questions

1. **Corpus provenance**: every row in `corpus_manifest.json` records a
   rights holder, a `license_class` and — for public-domain downloads — a
   verified sha256. Licensed text is installed from a file the family
   already owns and never redistributed; keep `data/bible/` gitignored and
   check that nothing licensed leaks into a chat card, a share image or
   the family feed.
2. **Reference parsing**: Scripture references are a grammar (ranges,
   chapters, books, abbreviations) and the in-house resolver now handles
   the forms BLB and the Bible App emit (`Gen 1:26-28; 3:15`, `Rom 3-12`,
   `Psa-Mal`, `Jn. 3.16`, `Zephaniah 3:17 ESV`). Keep widening it with
   real-world strings rather than trusting it: chip rendering, BLB deep
   links and TTS narration (`_expand_scripture_refs()`) all inherit its
   limits.
3. **Study-note joining**: commentary and footnotes are linked to verses by
   the ids the publisher's own markup uses, which is publisher-specific.
   An EPUB that renames its ids would join nothing. The import report
   names every cited block it could not find and every footnote nobody
   cites, and those counts are read on every install rather than trusted.
4. **File-layout extraction**: a PDF or EPUB that paginates differently
   from the sample can silently lose a verse. Both importers refuse
   anything that is not a complete 66-book Bible with the exact canonical
   chapter counts, and both print the paragraph classes they declined so
   a wrong guess about page furniture is visible.
5. **Jarvis question quality**: generated quizzes must be validated
   against the corpus server-side or the game economy breaks; budget a
   "question review" UX (flag a bad question → excluded for everyone).
6. **Audio**: a complete recorded NKJV narration already sits in the
   family's Nextcloud (~2.5 GB). That is the family's copy of licensed
   audio, so serving it from this server is the same arrangement as the
   study EPUB — but streaming it needs byte-range serving and per-verse
   indexing, which is a real project. Read-aloud ships first on the
   existing Kokoro/Edge TTS engine (free, already deployed); open question
   is whether Kokoro is good enough for long-form *listening* or is only
   acceptable as an accessibility/eyes-free aid. Cheap to test in Phase 1
   with `POST /execute/tts`.
7. **Typographic quality on long text**: Scripture is long-form reading,
   not a feed. Needs real typographic care (measure, leading, verse-number
   hierarchy, widow control). This is the single most visible quality bar
   in the whole feature — budget design time, not just engineering time.
8. **Kids mode**: YouVersion has a separate kids app; our first slice is
   the single Bible experience with child-depth Jarvis explanations. A
   kid profile (simplified UI, only-age-appropriate games) is a candidate
   follow-up, not v1.
9. **Reminders**: automation service job per user with timezone from
   `TZ`/user settings; YouVersion parity is a 7pm "you haven't opened the
   app" nudge — respect quiet hours and make dismissal sticky.
10. **Reading-event volume**: chapter reads are high-frequency compared to
   step events. Day-bucketed Redis counters keep it cheap, but the
   `ReadingEvent` table needs a retention policy (e.g. 400 days, matching
   the Redis TTL) or it becomes the biggest table in the system.
