# Bible Study

Status: **built** (service `services/bible`, UI `src/pages/Bible.tsx` and
`src/components/bible/*`). This document began as the plan; the
"Implementation status" section below records where the build differs, and
`services/bible/main.py` is the authority for routes. A Bible study experience built into
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
(chat/games surfaces and the chat envelope format),
`docs/GEO_SERVICE.md` (endpoint patterns), `CONTRIBUTING.md` (mobile parity,
fail-fast rules).

## Implementation status (2026-10-15)

What differs from the plan below:

- **Routes**: reading position is `PUT /state` (not `/state/position`);
  quizzes are games: `GET /games/{mode}`, `POST /games/{mode}/attempt`,
  `GET /games/leaderboard`; memorization is `GET/POST /memorize`,
  `DELETE /memorize/{verse_id}`, `GET /memorize/due`,
  `POST /memorize/{verse_id}/recall` (no "sets"); plans also have
  `GET /plans/{id}/family`, `POST /plans/{id}/catch-up` and day un-complete.
- **Page tabs** are **Read · Today · Play · Memorize · Progress**. Plans and
  the devotional live on Today; study help opens from a verse's action sheet
  rather than from a Study tab.
- **No Redis.** Every count, streak and badge is derived from the
  `ReadingEvent` rows in SQLite (each carries its `day`), and narration audio
  is cached in the `NarrationAudio` table. The compose file still passes
  `REDIS_URL`, but the service does not use it.
- **Read-aloud** is the bible service's own narration
  (`GET /narration`, `/narration/plan`, `/narration/chunk`, voices from
  `GET /voices`), proxied as `/api/bible/narration*`. There is no
  `/api/bible/speak`.
- **Jarvis study help** streams: `POST /study/ask/stream` (the reader uses it;
  `POST /study/ask` remains for one-shot callers), with a depth selector
  (child/teen/adult) and reference chips. A chip opens a popover with the
  passage text from `GET /passages` (`ReferenceChip.tsx`).
- **Family chat cards** use the envelope kinds the Talk handler already
  accepts (`activity`, `game`), posted through execution `POST /execute/talk`
  (`action: post_card`). No new `card_kind` values were added.
- **Blue Letter Bible links** use BLB's own scheme, checked for all 66 books
  (`services/bible/blb_links.py`): `/{version}/{book}/{ch}/{v}/` and the
  interlinear tab `t_conc_{chapter index × 1000 + verse}`, in the reader's
  translation when BLB has it, otherwise KJV. `GET /blb/link` takes
  `tool=interlinear` (other tool values are refused) and `version`.
- **Local interlinear**: admins import STEPBible TAGNT (Greek NT) / TAHOT
  (Hebrew OT) files (CC BY 4.0) in Bible admin → Interlinear
  (`POST /admin/interlinear/upload`, coverage at `GET /admin/interlinear`);
  `GET /interlinear?ref=` returns the words (original, transliteration,
  gloss, Strong's, grammar) or `available: false`, and the verse sheet falls
  back to BLB's interlinear tab. Source files:
  <https://github.com/STEPBible/STEPBible-Data> ("Translators Amalgamated OT+NT").
- **Share to Family chat** from the verse sheet: pick a conversation, add a
  note, sent through the Talk send path. `src/lib/verseShare.ts` is the one
  formatter for a verse as a chat line; the Family chat's **Verse** chip uses
  it too.
- **Reading reminder** is a loop inside the bible service
  (`services/bible/reminders.py`), not an automation job: every 5 minutes it
  finds readers whose `reminder_time` (server local time, `TZ` on the
  container) passed within the last **two hours** and who have not read today,
  and sends one telemetry `notify` with a per-day `once` key (so restarts never
  double-send). The push opens `/bible?ref=<today's plan reading>` (URL-encoded),
  `/bible?tab=memorize`, or `/bible`. A failed pass is logged and the loop
  carries on.
- **Marks** are fetched for the whole Bible and matched to a verse on book,
  chapter and verse (`components/bible/verseMarks.ts`), so a Genesis 3:16
  highlight never shows on (or is deleted from) John 3:16. Only highlights wash
  a verse; bookmarks and notes show a glyph.
- **Favourite translation** (`favorite_version`): the star marks the
  translation in hand; when reading another one, a "Back to …" button returns
  to the favourite in one tap. `split_view` picks how a comparison is laid
  out: `compare` (a row per verse) or `parallel` (two whole chapters in panes
  that scroll together, `ParallelReader.tsx`).
- **Offline**: besides the last 120 passages, the last answers for `/state`,
  `/versions` and `/books` are kept on the device (`withOfflineCopy` in
  `src/lib/bibleOffline.ts`, backed by `localStorage`), so a cold start with
  no connection still opens the saved chapter. If neither a server answer nor
  a copy exists, the Read tab says why with a retry instead of a never-ending
  skeleton.
- **Reader behaviour**: turning the page (buttons, swipe, Focus mode arrows)
  keeps Focus mode open and updates `?ref=` with a history *replace*.
  Read-aloud stops when the passage, translation or voice changes and when the
  reader leaves; closing "Ask about this verse" aborts the streaming answer.
  Stopping a plan asks first (starting again begins at day 1).

## Principles

1. **Reading is the product.** Everything else is a satellite. No surface
   may take the first screen, the first tap, or the daily rhythm away from
   reading the actual text.
2. **Reuse, never parallel-build.** Points, achievements, sharing, chat
   cards, notes, Nextcloud storage, RAG, push notifications, scheduled jobs,
   workspaces, the TTS engine and the LLM proxy all exist. The Bible
   feature is a new *domain*, not a new platform, and not a new way to
   store a note, award a point, schedule a reminder or notify a phone.
3. **Touch every existing service, on purpose.** §"SharedLLM service
   integration map" names the service, endpoint and payload for each
   integration. A new capability is only allowed to be new if no existing
   service already does it.
4. **blb.org is a study-tool base, not a content API.** Blue Letter Bible
   publishes no public REST API, only free web tools (ScriptTagger hover
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
7. **Mobile parity is the bar, not the exception** (see CONTRIBUTING.md).
   Every surface ships phone-first; desktop density is a layout variant of
   the same components.

### The hierarchy, stated once

- **Read**: the product. Offline-first, fast, typographically serious,
  remembers where you were.
  - **Marks**: highlights, bookmarks and notes (synced across devices).
  - **Plans**: a daily rhythm to give the reading a spine.
  - **Study**: BLB link-outs, cross-references, Jarvis explaining the passage.
  - **Memorize**: an SRS-lite recall ladder (a reading habit, not a course).
  - **Play**: daily quiz and family trivia (competition as seasoning, never
    the meal).
  - **Progress**: streaks, stats, achievements, sharing controls.

A user who only ever opens Read and never touches a single other tab must
feel the app is complete. That is the test for scope.

## Research findings

### YouVersion / Bible App (the "A LOT like" target)

Sources: youversion.com/bible-app, help.youversion.com (Plans, Streaks,
Plan with Friends), blog.youversion.com/getstarted.

| Feature | How they do it | What we take |
|---|---|---|
| **Plans** (devotionals/reading plans) | Duration in days, daily excerpts, sample, rating, "Save for Later", start-by-myself or **With Friends** (max 300, fixed start date, per-day progress visible to the group, private discussion area per day), "Catch Me Up" when behind, offline access, daily reminders | Plans with day checklists, progress bars, streak, catch-up, family plans with per-member progress + Talk-thread discussion |
| **Streaks** | App-Open streak (works offline, counts on reconnect) and Guided-Scripture streak (requires connectivity, resets if a day is skipped); reminder push at 7 pm local if not opened | App-open streak (offline-tolerant) + reading streak (≥1 chapter/day) + plan streak; reminder at a time the reader picks, sent by the bible service |
| **Personalization** | Highlights (colors), Bookmarks, Notes, all synced to the account, visible on any device | Same triad, per-user, in our SQLite |
| **Verse of the Day** | Daily verse + artist image, "See All" history, shareable | VOTD from the corpus (deterministic by date), share card |
| **Verse Images** | Tap verse → scripture-art card → share to SMS/WhatsApp/etc. | Share card rendered in-app + native share sheet (Capacitor) |
| **Friends feed / profile timeline** | Friends' activity (plan days, shares) in Home feed; activity timeline on profile | Opt-in activity feed (reuse geo `/activity/feed` pattern) scoped to `bible` |
| **Versions** | 1,200+ versions/900 languages; compare in parallel | Vendored public-domain set + optional api.bible upstream; side-by-side compare |
| **Audio** | Audio Bibles, listen offline | Recorded audio is licensed content (out of scope), but **our TTS engine already narrates Scripture**: `services/execution/tts.py` has a `BIBLE_BOOKS` table + `_expand_scripture_refs()` that turns "John 3:16" into spoken prose. Read-aloud is Phase 1, not Phase 7 |
| **Home screen / daily rhythm** | Verse of the Day at the top of Home; 7pm reminder; streak in the top bar | `bible_daily` widget + continue-reading, and the same reminder plumbing as every other notification |

### Blue Letter Bible (blb.org): the requested base

Sources: blueletterbible.org/webtools, /webtools/BLB_ScriptTagger.cfm,
blbclassic.org/freeoffer.cfm, study.bible/aboutBLB (researched 2026-10-02).

- **No official REST/JSON API exists.** Confirmed: the only documented
  integration surface is link/embed-based:
  - **BLB ScriptTagger**: free drop-in JS
    (`blueletterbible.org/assets/scripts/blbToolTip/BLB_ScriptTagger-min.js`)
    that scans a page for references (`Rom 1:16-18`, `Jhn 3:16`,
    `Deut 8:2-6, 10`, `Genesis 1:26-28; 3:15`, with optional translation
    abbreviation) and renders a hover tooltip with the verse text + a link to
    study at BLB. Config: `BLB.Tagger.Translation` (default NKJV),
    `HyperLinks` (`all|hover|none`), `HideTranslationAbbrev`,
    `TargetNewWindow`, `DarkTheme`, `Style` (`line|par`),
    `NoSearchTagNames`, `NoSearchClassNames`. **Tooltip caps at 7 verses**;
    longer ranges get a "More »" link to the chapter on blb.org. WordPress
    plugin available.
  - **BLB Search Forms**: a documented `GET` form to
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
    Faith's Checkbook), good link-out content for our Plans section.
  - **Deep-link URL shapes** (for our link-outs): passage
    `blb.org/{book}/{chapter}/{verse}` (e.g. `/jhn/3/16/`), lexicon
    `/lexicon/h3068/KJV/`, interlinear
    `/tools/interlinear/tr/jhn/1/1-51/`.
- **Unofficial scrapers exist but are off-limits**: GitHub downloaders that
  scrape BLB pages to JSON/TXT and third-party scraping APIs (e.g. Spider
  Cloud). They violate BLB's terms, are brittle, and would silently serve
  stale/copyrighted content; the repo's fail-fast doctrine forbids
  depending on an undocumented endpoint.
- **Consequence for the plan**: we do **not** scrape or reproduce BLB
  content (copyrighted). We (a) deep-link every study affordance to the
  right blb.org URL, (b) implement our own ScriptTagger-style reference
  popover for Jarvis answers and notes (same 7-verse cap, then "More »" to
  BLB; shipped as `ReferenceChip.tsx`, opened by a tap rather than a hover
  so it works on a phone), and (c) vendor public-domain text for the reader itself.

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
| **Vendored corpus** (KJV, ASV, WEB; US public domain) | Public domain | Primary reader text, offline, zero runtime dependency |
| **Family-owned licensed files** (NKJV, and ESV/NIV/NLT if the family supplies them) | Publisher copyright, non-redistributable | Read and study inside the household only; `license_class: "licensed"` marks the rows as do-not-redistribute |
| **api.bible** (scripture.api.bible) | CC/public-domain bibles free for non-commercial; per-translation commercial license otherwise; `api-key` header; REST: `/v1/bibles`, `/bibles/{id}/books`, `/chapters/{id}?content-type=json`, `/search?query=` | Online provider for extra translations, **declared in the manifest** (`providers`) rather than hard-coded, and keyed by Identity `GlobalSetting` `bible_api_key`: **no default, fails loudly when unset**. **The API host is `api.scripture.api.bible`, not `api.bible`**; the latter is the marketing site and 404s every `/v1/*` path. `services/bible/providers.py` fetches a translation on demand at import time and caches it like any other translation; **nothing on the read path touches the network**. Verified live: 40 English translations on a free key, including **NIV** and **NLT**; **ESV and NKJV are not in the catalogue** and still need publisher files |
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
| `services/bible/import_pdf.py` | A PDF | Verses. Detects two-column layouts, ignores running heads and page numbers, and only accepts a verse number that is the *expected next* one, which is what stops a page number from becoming a verse |
| `services/bible/import_epub.py` | An EPUB | Verses **and** study notes |

The EPUB route is what makes a study Bible a study Bible here. It walks the
documents in spine order and, for every element carrying a verse anchor
(`id="vBBCCCVVV"`, which encodes book/chapter/verse), collects the text from that
anchor to the next. Verse continuations in sibling paragraphs are recognised by
class so poetic books (`Psalms`, `Job`, `Song of Songs`) do not lose a line, and
every paragraph class that was *declined* (footnotes, commentary, sidebars,
concordance, glossary, front matter) is reported by name in the import output,
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
refuses it; that is what catches a truncated or mis-paginated file. A provider is
authoritative about its own versification and simply omits verses that textual
criticism treats as later additions: NIV2011 has **no Matthew 17:21** at all, nor
Mark 7:11-12. So `parse_source(..., allow_gaps=True)` is set **only** on the
provider path. The blank slot is kept as `""` so every verse still carries its
printed number, `corpus.omitted_verses()` names each one, and the import log
reports them (`43 verses are absent from this translation by design (Matthew
17:21, Mark 7:11, …); they keep their printed numbers`). `BibleVerse` is keyed on
`(version, book, chapter, verse)`, so a gap shifts nothing. It only means asking
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
  installs nothing, so a parsing change can be checked for one request instead of
  1,189. The chapter it fetched stays in the cache, so the real run does not pay
  for it again.
- A fetch that still runs into the cap is refused cleanly: the operator sees the
  API's own `Monthly limit exceeded. Please upgrade your plan.` and the `ImportRun`
  records it, instead of installing half a Bible.

### The provider cache

Every chapter that comes back from a provider is written to
`BIBLE_PROVIDER_CACHE` **before** it is parsed, and it is kept **write-once**.
Scripture does not change, so a chapter fetched once is never fetched again: not
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
`bible.db`. A fully cached translation (book list included) costs **zero**
requests, and a run killed at book 40 costs nothing at all, which is the failure
that previously wasted whole allowances.

The provider is asked for **`content-type=json`**, which returns a node tree: a
`verse` tag whose only content is the verse number, with the actual words in
sibling nodes carrying `attrs.verseId`. So a provider verse runs **from its marker
to the next marker** (the same rule the EPUB importer already uses), and
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
chapter, verse and kind, and is **never merged into `BibleVerse.text`**.

A note's identity includes the study Bible it was written for, because
several study Bibles sit on the same words: the Nelson and the MacArthur
commentaries on NKJV are different arguments about the same verses, and one
must never quietly replace the other. That is why the corpus has two axes
rather than one:

| Axis | Code | Meaning |
|---|---|---|
| Translation | `BibleVersion.code` (`nkjv`) | The words. Imported once, keyed by translation alone, never duplicated |
| Study Bible | `BibleEdition.code` (`nkjv-tmn`, `nkjv-macarthur`) | Whose commentary. Any number of these per translation |

Every installed translation always gets at least one edition (an implicit
text-only row named after the translation), so "this translation has no
notes" is an answerable question rather than an empty list. The **edition** is
the default for a translation, because defaulting to "the one with the most
notes" would silently change what a reader sees after a re-import.

### One translation is primary

The manifest marks exactly one translation `primary`, and the loader refuses a
manifest that marks two. Reading defaults to it: `corpus.default_version_code()`
returns the primary when it is installed, otherwise the installed translation
with the most verses, otherwise `""`, which the service reports as a 503 with
its own message rather than picking something. `_default_version` and the verse
of the day both go through that one function, so the daily card cannot quietly
come from a different translation than the reader.

NKJV is the primary. It is the one the family owns, and the one carrying the
study notes.

### Installing more: Admin › Bible

Four ways in, because the family's sources differ, all through
`services/bible/importer.py` (one code path, with the three CLIs reduced to
thin callers):

| Way | Route | What it needs |
|---|---|---|
| Online provider | `GET /admin/providers/{code}/estimate` for the cost, `GET /admin/providers/{code}/translations` to list, `POST /admin/imports` with `provider`/`provider_id` to install | `bible_api_key` (or whatever the provider declares). The catalogue is a *separate* call so listing it never needs the network and an unreachable provider produces one message in one place. Install is disabled until the estimate is in and the run fits the budget |
| **The Calibre shelf in Nextcloud** | `POST /admin/library` with `path` to browse, `POST /admin/imports` with `library_path` to install | `CALIBRE_LIBRARY_PATH` and `BIBLE_IMPORT_DIR`. Browse first, then install a file you can see (see below) |
| Upload from the browser | `POST /admin/imports/upload` (multipart) | `BIBLE_IMPORT_DIR`. The extension is checked **before** anything is written; a 256 MB limit streams in 1 MB chunks and removes the partial file if it trips |
| A path on the server | `POST /admin/imports` with `source_path` | The path, for a file already downloaded somewhere the service can reach |

Every run is recorded in `ImportRun` **including the failures**. The refusals
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
one, with no credentials in the browser and no command to type.

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
| Extra translations | Family-supplied PDF/EPUB **or** the api.bible provider, declared in `corpus_manifest.json` and installed from Admin › Bible | Publisher text is read inside the household, never redistributed. The provider is configuration, not code, and needs a key before it is offered; an unconfigured provider is shown with the setting to set rather than hidden |
| Primary translation | Manifest marks one translation `primary`; reading and the verse of the day both default to it | "Whichever translation sorts first" is not a decision anyone made. One marked translation makes the default explicit, and `default_version_code()` falls back to the richest installed rather than to a guess |
| Study material (commentary, footnotes, introductions) | Separate `StudyNote` table, joined to verses on demand via `GET /study/notes`. A verse that carries notes is marked in the reader; the notes themselves load only when asked | A reading app that mixes a commentary into the text is worse at reading. Keeping them apart is what lets one reader serve both a plain KJV and a study NKJV |
| A verse that has a note of your own | A mark of its own (`kind: "note"`, alongside `highlight`/`bookmark`) pointing at the Nextcloud path the note handler reported | Writing a note is not highlighting. A reader must be able to see which verses they have written about without opening each one, and the pointer must not be a guess at the filename; the server reports the path it actually wrote |
| Several study Bibles per translation | Translation and study Bible are separate columns (`BibleVersion.code` vs `BibleEdition.code`); the reader picks which one explains the text | The Nelson and MacArthur study Bibles are both NKJV and disagree. Collapsing them into one `version_code` would make re-importing one delete the other's notes |
| Notes from another translation | Opt-in `cross_version`, default **off**, remembered per user as `cross_version_notes`. Off means only notes written for the translation in hand; on adds the others, each labelled with its translation and study Bible | A note on the NIV wording is not a note on the NKJV wording. Merging them silently would put arguments about different words under the same verse. Off by default; the toggle names exactly which study Bibles it would add |
| Study depth (Strong's/lexicon/interlinear) | blb.org deep links + ScriptTagger-style hover | BLB explicitly offers this; reproducing their content is not |
| Verse fetching beyond the vendored corpus | api.bible at `api.scripture.api.bible` (key-gated) or BibleGet (`appid`-identified, cached) | Both documented and reachable; never an undocumented BLB scraper. Which translations a key unlocks varies by licence, so the catalogue is fetched rather than assumed |
| Jarvis cross-references | BibleGet `/v3/search/similar` + corpus-internal cross-refs | "Verses like this" is a study superpower and needs no model call |
| Per-user state | New `services/bible/` with SQLModel/SQLite; counts and streaks derived from `ReadingEvent` rows keyed by day | Mirrors geo; keeps Identity from growing a second activity schema |
| Achievements | Data-driven `services/bible/achievements.json` + rules engine (`achievements.py`, `metrics.py`), same pattern as `services/geo/achievements.json`; **derived, not stored** | Bug fixes instantly correct history; new badges need no deploy |
| Points/currency | Award into the **existing** `geo:points:{user}` ledger via geo's internal endpoint (reason `achievement`/`game`) | Two currencies, one ledger: chores, fitness and Bible reward the same vault, stars mirror to Skylight |
| Family sharing | Extend `UserActivitySharing` scopes with `bible`; reuse audience enforcement (circle/users, 404-when-not-consented) | One consent boundary, already tested; a second source of truth for "may I see this user" is how checks drift |
| Family plans | Planned as a `PlanGroup` table; built instead as "reading along": family members on the same plan see each other's progress through `GET /plans/{id}/family`, consent-gated | Play happens where the family already is; no second inbox |
| Quizzes/games | Deterministic generators built from the installed text (`services/bible/games.py`, curated refs in `games/verses.json`); Jarvis-generated questions only when the server can verify the answer key against the corpus (not built) | A game that hallucinates a wrong answer breaks trust; the server is the validator |
| Memorization | SRS-lite interval ladder + recall levels, per-verse state (`memorize.py`, `MemoryVerse`) | Simple, explainable, no ML needed |
| Jarvis study assist | Existing gateway `/v1/chat/completions` with a study system prompt, called by the bible service. The prompt is **the passage and its `StudyNote` rows plus the reader's question**, nothing else, and the model is asked to say plainly when they do not answer. Over-long questions and passages are refused rather than truncated; failure is a 503 with the upstream's own words, never a placeholder answer | The answer can always be traced to something on screen, and a question the passage does not answer is a real answer rather than a hallucination. Reuses Raven/LLM plumbing, so the provider can change without a bible deploy |
| Read-aloud | **Existing** `POST /execute/tts` (Kokoro + Edge-TTS); `_expand_scripture_refs()` already narrates "John 3:16" as prose, and SSMD single- and double-pipe pauses give a good reading cadence. Built as the bible service's own `/narration*` routes (proxied as `/api/bible/narration*`), cached per translation+reference+voice in `NarrationAudio` | We own no new engine and no audio licenses. "Play on the kitchen speaker" would ride the existing `/execute/ha_service` `tts.piper` path (not built) |
| Note storage | **Nextcloud, via the Notes handler.** The note body is written by the existing `/execute/note` handler into `Notes/{Title}.md` or `{category}/{Title}.md`; the `note` mark stores the path the handler reported (`note_path`) plus a one-line preview. If the server does not report a path, the verse is **not** marked and the reader is told, rather than being pointed at a filename the client guessed | Bible notes are visible in Notes, searchable in RAG, shareable with the family folder, and editable by hand. Two copies of a note is how they drift apart |
| Sharing controls | Extend `ActivitySharingPanel` with a `bible` scope (4th checkbox next to `totals`/`workouts`/`achievements`) | Private by default; audience `circle`/`users` already enforced server-side |
| Reminders | **Bible-service loop** (`reminders.py`) + **telemetry** `/api/telemetry/notify` for delivery, in-app outbox as the durable record. (Planned as an automation job; automation has no timer-create API and its daily recurrence drifts across DST.) | No new notification infra; the reminder goes through the same notify path as every other notification |
| Study memory | **RAG** ingest of promoted notes and study threads, with a stable citable `id` per lesson (`handlers/learning.py` pattern). **Built:** saved study turns (`bible-turn-*`), verse notes promoted from the reader ("Remember in study memory", `bible-note-<mark id>`) and reading-plan reflections (`bible-plan-<plan>-<day>-<user>`, kept by the bible service in `PlanReflection`), all in the `bible_study` collection; re-saving replaces the memory | "Show me where we already talked about this" is the study superpower, and the assistant should not re-teach what the family already covered |
| Study workspace | **workspace_runtime** opt-in repo for `study/journal`, `memory/sets/*.json`, quiz results. Not built | Lets a family diff/version their own study material; skipped with a visible note when no workspace is enabled |
| Home entry point | `bible_daily` dashboard widget + continue-reading, mirroring how Home opens with Verse of the Day | The reader must be one tap from Home, the way the Bible App opens on the text |
| UI surface | New `/bible` page + `bible_daily` dashboard widget | One page, one widget; nav wired through `components/layout/navItems.ts` like every feature |

## SharedLLM service integration map

Every existing service, and exactly what the Bible feature asks of it.
"Change required" rows are the concrete edits to existing files (collected
in §"Changes to existing code"). Rows marked **planned** describe an
integration that is designed but not built yet.

| Service | What the Bible feature uses it for | Endpoint / mechanism | Change required |
|---|---|---|---|
| **gateway** (`:11435`) | Every `/api/bible/*` route as a thin BFF proxy (identity resolution → `X-Internal-Secret` → JSON pass-through), including read-aloud | Same pattern as the `/api/geo/*` proxies | Route block per endpoint; read-aloud via `/api/bible/narration*` |
| **identity** (`:8001`) | User identity; `bible_svc_url`; `bible_api_key`; `blb_base_url`; the `bible` sharing scope; widget layout rows | `GET/PUT /api/widgets/settings`, `/api/global-settings`, `GET /api/internal/activity-sharing`, `PUT /api/users/me/activity-sharing` | New `GlobalSetting` keys; `ActivitySharingPanel` gains a 4th scope entry |
| **geo** (`:8009`) | **Points currency.** Every Bible award and every game score is banked in the existing `geo:points:{user}` ledger, which already mirrors to Skylight stars. One vault, three sources (chores / fitness / Scripture) | `POST /api/geo/stars` (`reason: achievement` \| `game`) | None: call it. Skylight write-through is reported, not assumed, so a Skylight outage can't lose an award |
| **geo achievements engine** | The *pattern* copied for the bible achievements: derived-not-stored, `id`/`name`/`description`/`points`/`rule{type,value}`, banked on first read so `earned_on` is stable | `services/geo/achievements.py` + `achievements.json` (19 entries, 9 rule types) | New file `services/bible/achievements.json` (20 entries, 8 rule types) + rules engine in the bible service |
| **geo sharing enforcement** | Cross-user reads (family plan board, "what's Dad reading?") must pass the same consent gate: 404 for a non-consented user, feed omits non-consented, never errors | `GET /api/internal/activity-sharing` (audience `circle`/`users`, `user_ids`, `share[]`) | `share[]` gains `"bible"` |
| **execution** (`:8003`) | **TTS read-aloud**: `POST /execute/tts` returns `audio_base64` + `mime_type: audio/wav`, and `_expand_scripture_refs()` already speaks references naturally; voices list for the reader's voice picker; **Nextcloud notes**; family chat cards | `POST /execute/tts`, `GET /execute/tts/voices`, `/execute/note`, `POST /execute/talk` (`action: post_card`) | None: call it. A missing engine raises `FileNotFoundError("Kokoro voices missing: …")`, surfaced as a visible reader state, never a silent no-op |
| **execution → Nextcloud** | **Study notes are Notes.** A verse note is written to Nextcloud in the same folder structure the family already browses | gateway `/api/communication/notes/*` → execution `/execute/note` (`storage=nextcloud`, `Notes/{Title}.md` or `{category}/{Title}.md`) | None: call it. The bible service stores only the *index* (ref + Nextcloud path); the note body stays in Nextcloud so Notes and Bible never fork a copy |
| **execution family games** (planned) | **Talk games**: a family could run a Bible trivia round in the chat room where they already are, leaderboard in cards, one star per correct answer | `handlers/family_games.py` + gateway `POST /api/communication/talk/game` | A Bible question set as data, with answer validation against the corpus before a question is ever offered. Not built |
| **family chat / Talk** | **The Bible happens in the family channel.** Daily quiz scores, finished plans, mastered verses and badge unlocks land as typed cards (sharers only), with no second inbox | Typed envelope (`src/lib/chatEnvelope.ts`), execution `POST /execute/talk` with `action: post_card` (`card_kind`/`card_title`/`card_detail`/`card_stars`/`card_stats`) | None: cards use the existing `activity` and `game` kinds. Silent when `FAMILY_CHAT_TOKEN` is unset (existing `announce_awards` behavior) |
| **storage** (`:8005`) | The Calibre shelf in Nextcloud as an import source; the bytes travel through storage so it keeps the only copy of the Nextcloud credentials | `POST /providers/list`, `POST /providers/fetch` | `get_bytes` on the provider ABC and the fetch route (see §"Changes to existing code") |
| **rag** (`:8004`, planned) | **Study memory.** Promoted notes, plan reflections and Jarvis study threads would be ingested so the assistant recalls what *this family* has already studied. Search across collections (`collection_name: "all"`, hybrid BM25+vector) would power "show me where we talked about patience" | `POST /rag/ingest`, `POST /rag/search` (`collection_name`, `alpha`, `use_rrf`), `/rag/learning` | Ingest compact JSON with a stable citable `id` (`lesson-<sha1[:10]>`) exactly as `handlers/learning.py` does. Not built |
| **automation** | Not used. The reading reminder runs inside the bible service (`reminders.py`) | n/a | None. `DISPATCH_TARGETS` was left as is |
| **telemetry push** | Delivery for the reading reminder | `POST /api/telemetry/notify` with a per-day `once` key; telemetry delivers through `send_to_user` (webpush VAPID, FCM fallback) and the in-app outbox | None: call it. Push failure never loses the notification (it is already in the outbox) |
| **workspace_runtime** (`:8007`, planned) | **Study workspace.** An opt-in sandboxed git repo per workspace where the Bible feature would write machine-readable artifacts (`study/journal/`, `memory/sets/*.json`, quiz results) so a family can diff, review and version their own study material | `/workspaces`, `/files/read`, `/files/write`, `/files/list`, `/workflow/write-sync-commit`, `config/workspaces.json` | None: call it. Opt-in per workspace; skipped with a visible note when no workspace is enabled. Not built |
| **logging** (`:8006`) | Awards, plan completions, quiz attempts and failed upstreams are structured log lines | existing structured logger | None |
| **control_plane** (`:8008`) | Deploy/health parity for the new service: it is a first-class citizen, not a sidecar | standard image/healthcheck flow | None beyond the standard compose entry |
| **ui / Capacitor** | Desktop + Android + iOS from one React tree; native share sheet for verse images; home-screen widget; haptics | `App.tsx` routes, `components/layout/navItems.ts` (Sidebar/BottomNav/Header search), `useHaptics`, `Capacitor.isNativePlatform()`, `ProtectedRoute` | New `/bible` route + nav entry; `bible_daily` widget registration |
| **arcade** (`docs/ARCADE_INTEGRATION.md`) | Existing admin-curated arcade titles stay as-is; Bible games live in the Bible › Play tab, not bolted onto the arcade catalog | n/a | None |

### Integration rules that fall out of this map

- **One ledger, one inbox, one notes store, one push path.**
  Points go to geo; conversation goes to Talk; note bodies go to Nextcloud;
  delivery goes to telemetry. The Bible service owns *reading state and
  reading events* (and its own reminder loop) and nothing else.
- **The bible service never calls an external API on the read path.**
  `api.bible`/`BibleGet`/`blb.org` are enrichment, reached only when the
  user asks for something outside the vendored corpus.
- **Content never enters a no-opt-in store.** Events carry a reference
  (`John 3:16`, `rom`, `quiz_correct`), never note text or highlights;
  Identity's `FeatureUsage` is metadata-only by design.
- **Every optional integration degrades loudly.** Unconfigured → visible
  "not configured: `<setting>`" state naming how to fix it, never a silent
  skip and never a canned substitute.

## Backend architecture

### Service: `services/bible/` (port 8010)

Same skeleton as geo: FastAPI, `lifespan`, `X-Internal-Secret` middleware
(global require), `info_router` from `services.shared.info_endpoint`,
SQLModel/SQLite at `/data/bible.db` (volume `./data/bible:/data`).

Compose entry (`docker-compose.yml`, service `bible`): image
`ghcr.io/jmiahman1/sharedllm-bible:latest`, env `INTERNAL_SECRET`,
`FERNET_KEY`, `NETWORK_MODE=bridge`, `BIBLE_DATABASE_URL`, `BLB_BASE_URL`,
`BIBLE_DEVOTIONAL_DIR`, `BIBLE_API_KEY`, `BIBLE_PROVIDER_CACHE`,
`BIBLE_PROVIDER_CALL_BUDGET`, `CALIBRE_LIBRARY_PATH`,
`IDENTITY_SVC_URL=${BRIDGE_IDENTITY_SVC_URL}`,
`EXECUTION_SVC_URL=${BRIDGE_EXECUTION_SVC_URL}` (TTS read-aloud, chat cards),
`STORAGE_SVC_URL=${BRIDGE_STORAGE_SVC_URL}` (the Calibre shelf),
`GEO_SVC_URL=${BRIDGE_GEO_SVC_URL}`,
`REDIS_URL=${BRIDGE_REDIS_URL}` (passed but unused),
`GATEWAY_INTERNAL_URL=http://gateway:11435`, `TZ=America/Phoenix`,
volume `./data/bible:/data`, `depends_on: redis`.
`BRIDGE_BIBLE_SVC_URL` is in `.env.example`, `_net_url("BIBLE", …)` in
`services/config.py` reads it, Identity seeds `bible_svc_url`, and the
`Caddyfile` routes `:8010` to `bible:8010`.

### Changes to existing code

The concrete edit list implied by the integration map. Each row is a small,
testable diff.

| File | Change | Test |
|---|---|---|
| `services/ui/src/components/settings/ActivitySharingPanel.tsx` | 4th scope entry `{id:'bible',...}` in the literal scope array; default stays `share: ['totals']` (private by default) | `src/test/ActivitySharingPanel.test.tsx` + `ActivitySharingPanelNonAdmin.test.tsx` |
| `services/identity/models.py` | `DEFAULT_GLOBAL_SETTINGS` seeds `bible_svc_url`, `blb_base_url`, `bible_devotional_dir`, `bible_import_dir`, `bible_provider_cache`, `bible_provider_call_budget`, and `bible_api_key` (masked in the UI) | identity settings test |
| `services/gateway/main.py` | `/api/bible/*` proxy block (read-aloud via `/api/bible/narration*`) | `services/gateway/tests/test_bible_proxy.py` |
| `services/execution/handlers/note.py` | No change; the reader passes `category: 'Bible'` so Bible notes land in `Bible/{Title}.md` and stay separable from other notes | existing notes tests |
| `services/geo/achievements.py` | No change; `reason` already accepts `achievement`/`game` | existing star tests |
| `services/storage/providers.py` | `StorageProvider.get_bytes(path)` on the ABC. `get_content` decodes bytes as text, so a PDF/EPUB comes back as replacement characters rather than an error. `None` means "this provider cannot give you bytes", which is a different answer from "the file was empty" | `test_a_provider_that_cannot_give_bytes_declines_instead_of_guessing` |
| `services/storage/providers_impl/nextcloud.py` | `get_bytes` → `NextCloudClient.get_file_bytes` (the binary-safe path; `get_file_content` mangles binaries) | `test_storage_provider_fetch.py` |
| `services/storage/providers_impl/calibre.py` | `get_bytes` returns `None`: the Calibre provider is an **index, not a file server**, so declining is the honest answer | `test_a_provider_that_cannot_give_bytes_declines_instead_of_guessing` |
| `services/storage/models.py` + `main.py` | `ProviderFetchRequest(provider, path, max_bytes)` and `POST /providers/fetch` returning `{status, path, name, size, content_b64}` behind `X-Internal-Secret`. 413 over the ceiling (both numbers named), 502 when `get_bytes` returns `None`, 422 when `max_bytes <= 0` | `services/storage/tests/test_storage_provider_fetch.py` |

Planned edits that turned out unnecessary: automation's `DISPATCH_TARGETS`
did not gain `bible` (the reminder is a loop in the bible service), and
`chatEnvelope.ts` did not gain Bible-specific `card_kind` values (cards use
the existing `activity` and `game` kinds).

Cross-reference: the shelf itself, and why it is indexed read-only, is
documented in [`docs/CALIBRE_LIBRARY.md`](CALIBRE_LIBRARY.md).

### Data model (`models.py`)

```
BibleVersion       code(unique), name, language, license_class, verse_count, imported_at
BibleEdition       code(unique), version_code, name, publisher, language,
                   license_class, rights_holder, note_count, note_kinds, imported_at
BibleBook          version_code, osis, name, order, chapters
BibleVerse         PK(version_code, osis, chapter, verse), text
StudyNote          id, version_code, edition_code, osis, chapter,
                   verse(0=chapter-wide),
                   kind(commentary|footnote|introduction|heading),
                   ordinal, body, source, imported_at
Devotional         id, source, work, day_of_year, title, reference, text, kind, url, month, day
UserBibleState     username PK, last_book/chapter/verse, last_open_day, last_read_at,
                   default_version, default_edition, favorite_version, compare_version,
                   cross_version_notes, show_notes, font_scale, line_height,
                   theme(serif|sans), page, reminder_time, read_aloud_voice,
                   split_view(compare|parallel)
VerseMark          id, username, version_code, osis, chapter, verse_start, verse_end,
                   kind(highlight|bookmark|note), color, note_path(nextcloud),
                   note_preview, created_at, updated_at
NarrationAudio     key PK, version_code, reference, voice, chunk_index, verse_count,
                   mime_type, audio(bytes), created_at
InterlinearVerse   id, book, chapter, verse, language, words JSON, source, imported_at
ImportRun          id, source, kind, code, name, provider, provider_id, status, message,
                   verse_count, book_count, note_count, log, duration_ms, created_at
ReadingEvent       id, username, kind, ref, day, value, created_at   (kinds below)
AchievementEarned  PK(username, achievement_id), points, earned_on
GameScore          id, username, mode, day, seed, version_code, correct, total,
                   duration_ms, counted, answers, created_at
MemoryVerse        id, username, ref, osis, chapter, verse_start, verse_end,
                   version_code, set_id, level, due_on, reviews, lapses,
                   last_score, created_at
PlanEnrollment     id, username, plan_id, started_on, completed_days, finished_on,
                   active, created_at
```

`default_version` is where the reader is; `favorite_version` is the one they
come back to; `compare_version` is the one shown *beside* the text. Three
separate fields because they are three separate decisions: turning a
comparison off must not move the reader's own text. `compare_version` blank
means no comparison and is never guessed, and comparing a translation against
itself is refused, because the same words twice reads as a bug rather than as
a comparison. `split_view` is reserved for the *layout* of that second column
(`compare` = a row per verse, `parallel` = the whole second chapter in its
own pane, scrolled in step with the first). The Compare control's **Rows /
Panes** switch writes it.
`show_notes` is the reader saying they want the chapter's commentary on the
reading surface rather than behind a tap; it defaults off so the reader opens
as text, and turning it on fetches nothing extra because the chapter's notes
are already loaded to mark which verses carry study material.

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
MacArthur commentary with it. The two reader choices, **which study Bible**
and **notes from other translations**, are both resolved in one place,
`study._edition_filter()`, so there is exactly one answer to "which notes
apply here" no matter which endpoint asks.

Because `BibleEdition` and the newer `UserBibleState` columns are additive,
`services/bible/migrations.py` brings an existing corpus forward in place: it
adds the missing columns, adopts notes with no edition into one named after
their translation, and inserts a text-only edition for every translation that
had none. It reports what it did, is safe to run twice, and runs in the service
lifespan after `create_all`, so an existing `bible.db` upgrades on restart
with no re-import and no lost verses.

The note count and kinds on an edition row are a convenience so the picker never
has to group over tens of thousands of notes, which makes them a cache that can
drift, and a stale count reads to a reader as "this study Bible has no notes".
So they are treated as derived: `corpus.refresh_edition_notes()` recounts them
from the stored rows and returns only the editions it actually had to correct,
and `python3 -m services.bible.editions list` runs that before printing rather
than reporting a number it has not checked. `rename` refreshes its own row
because a rename moves the notes without rewriting the summary.

Counts, streaks and badges are computed from `ReadingEvent` rows (each carries
the ISO `day` it happened on) in `services/bible/metrics.py`; nothing about
them is stored except the date a badge was first banked (`AchievementEarned`).
Narration audio is cached per translation, reference, voice and chunk in
`NarrationAudio`, so re-listening a chapter does not call the TTS engine again.

**Reading events** (the "A LOT like the Bible App" tracking layer), as
accepted by `POST /events`:
`app_open`, `chapter_read`, `chapter_complete`, `verse_tapped`, `search`,
`note_created`, `plan_day_completed`, `plan_started`, `quiz_played`,
`quiz_correct`, `memory_recall`, `game_played`, `assistant_ask`,
`share_tap`, `blb_link_tap`, `devotional_opened`. The server also records
`mark_created` itself when a mark is saved. Events carry a *reference*,
never note/highlight *content*, into any no-opt-in store. Identity's
`FeatureUsage` is metadata-only by design (`models.py` consent boundary),
so Bible usage lives in this service, visible to the user always and to the
family only via the opt-in scopes.

### Endpoints (service, proxied by the gateway under `/api/bible/*`)

| Endpoint | Purpose |
|---|---|
| `GET /versions` | Manifest catalogue joined with what is installed, including catalogued-but-absent translations and why |
| `GET /books?version=` | Books of a translation, with chapter counts |
| `GET /passages?ref=John 3:16&version=` | Reference resolver and reader text (also used by Jarvis chips) |
| `GET /search?q=&version=&book=&limit=` | Full-text over corpus |
| `GET /marks`, `PUT /marks`, `DELETE /marks/{mark_id}` | Highlights/bookmarks/notes for the caller |
| `GET /state`, `PUT /state` | Last-read position, prefs |
| `POST /events` | Record one reading event (metadata only) |
| `GET /stats?days=` | Own usage totals (1 to 400 days, default 30) |
| `GET /streaks` | App-open and reading streaks |
| `GET /plans`, `GET /plans/{id}`, `POST /plans/{id}/start`, `DELETE /plans/{id}` | Plan catalog, start and stop |
| `POST /plans/{id}/days/{day}/complete`, `DELETE /plans/{id}/days/{day}` | Complete or un-complete a plan day |
| `POST /plans/{id}/catch-up` | Mark missed days complete in one tap |
| `GET /plans/{id}/family` | Family members on the same plan and their progress (consent-gated) |
| `GET /verse-of-day?day=&version=&scope=all\|ot\|nt` | Deterministic-by-date verse + share card data, including a stored context note when one exists |
| `POST /verse-of-day/context` (`{day?, version?, scope?, refresh?}`) | Jarvis's plain-language context for the Verse of the Day. Generated on demand from the surrounding chapter (±8 verses) and installed study notes, cached per translation+verse, and returned with the verse. `refresh` asks for new words; a failed refresh keeps the old note. Gateway: `POST /api/bible/verse-of-day/context`, caller-scoped, 300s timeout |
| `GET /devotional`, `GET /devotional/sources` | Today's devotional, and every source with the reason any was skipped |
| `GET /daily` | Verse, devotional, streaks and plan line in one call (the `bible_daily` widget) |
| `GET/POST /memorize`, `DELETE /memorize/{verse_id}`, `POST /memorize/{verse_id}/recall` | Memory verses + record a recall result (advances SRS) |
| `GET /memorize/due` | Due queue |
| `GET /games` | The game modes |
| `GET /games/{mode}` | Generate a round |
| `POST /games/{mode}/attempt` | Score an attempt (server regenerates the round and re-checks answers) |
| `GET /games/attempts/{attempt_id}` | One scored attempt |
| `GET /games/leaderboard?day=` | Today's daily-quiz scores for the viewer and every family member who shares with them |
| `GET /achievements` | Earned + next-up with progress (`4/7 days`) |
| `GET /activity/summary?username=&target=` | Own, or a **consented** user's, scope-filtered totals (404 otherwise) |
| `GET /activity/feed?limit=` | Opt-in activity of others (feed omits non-consented, never errors) |
| `GET /study/notes?ref=&version=&kind=&edition=&cross_version=` | Study apparatus for a passage (commentary, footnotes, introductions), fetched on demand. `edition` picks which study Bible; `cross_version` (default off) adds notes written for other translations. Returns every installed edition and every other translation that carries notes, so the client can render the choices |
| `GET /editions?version=` | Study Bibles installed for a translation, the default one, and which other translations carry notes |
| `POST /study/ask` (`{question, ref, version?, edition?, cross_version?, depth?}`) | **Jarvis study answer, grounded in what is on screen.** The server assembles the prompt from the passage's verses and its own `StudyNote` rows and asks the gateway LLM; the reader's question is the only free text. An empty question, one over 2,000 characters, or a passage over 150 verses is a 400 naming the number; at most the first 40 notes go into the prompt, each cut at 1,200 characters. Only the reading event is recorded (`kind`, `ref`, `value`, `day`), **never the question**, so a study question cannot resurface in activity. Answered in the same voice as `GET /study/notes` (400 for the reader's mistake, 503 for the operator's) and **never a placeholder answer**: an unreachable model, an HTTP error, a non-JSON body or an empty reply each raise a named 503 carrying the upstream's own words. Gateway: `POST /api/bible/study/ask`, caller-scoped (no `user_id`), 300s timeout |
| `POST /study/ask/stream` | The same answer, streamed; the reader's Ask panel uses this one |
| `GET /blb/link?ref=&tool=&version=` | Pre-built blb.org verse or interlinear URL (never proxies BLB content) |
| `GET /interlinear?ref=` | Imported word-by-word interlinear, or `available: false` |
| `GET /voices` | Narration voices from the execution TTS engine; an absent engine is a 503 naming `EXECUTION_SVC_URL` |
| `GET /narration?ref=&version=&voice=` | The passage as speech, cached per translation+reference+voice. Refuses rather than truncating: over 120 verses or 12,000 characters is a 400 naming the number |
| `GET /narration/plan`, `GET /narration/chunk` | The passage split into pieces, and one piece as audio, so playback starts before the whole chapter is spoken |
| `GET /admin/imports` | **Admin.** Installed catalogue with `primary`/`provider`, declared providers with their configuration reason, supported kinds, the import directory, and the import history |
| `GET /admin/providers/{code}/translations` | **Admin.** What one provider can supply. Separate from the catalogue so listing it never needs the network |
| `GET /admin/library?path=`, `POST /admin/library` | **Admin.** One folder of the Calibre shelf: folders first, then files, each marked installable or carrying the reason it is not. An unreadable file is **shown with its reason**, not hidden. The gateway exposes only `POST /api/bible/admin/library` (a WebDAV folder name carries slashes and can carry semicolons, so it is the wrong thing for a query string) |
| `GET /admin/providers/{code}/estimate?translation_id=` | **Admin.** What installing it would cost: chapters cached vs. remaining requests, the budget, and whether this run would be refused. Asked **before** the Install button is offered, not after |
| `POST /admin/imports` | **Admin.** Install from `source_path`, from `provider`/`provider_id`, or from `library_path`. 200 on success, **422 carrying the refusal** |
| `POST /admin/imports` with `library_path` | **Admin.** Fetch one file off the shelf and import it through exactly the same code an upload would use. The fetch log line comes first so the operator can see the bytes arrived before the refusal |
| `POST /admin/imports/upload` | **Admin.** Install a picked file. Extension checked before any write; 256 MB cap with the partial file removed |
| `POST /admin/imports` with `dry_run: true` | **Admin.** Fetch one book (Genesis 1), parse it, show the text and install nothing: one request instead of 1,189. The sampled chapter stays in the cache |
| `GET /admin/interlinear`, `POST /admin/interlinear/upload` | **Admin.** Interlinear coverage, and import of a STEPBible tagged text (`.txt`/`.tsv`) |

Cross-service rows (the bible service is the caller; these endpoints live
in the services named, and the gateway proxies them so the browser never
talks to an internal service directly):

| Called service | Endpoint | Purpose |
|---|---|---|
| execution (TTS) | `POST /execute/tts`, behind the bible service's `/narration*` | Read-aloud for a chapter/ref, cached per translation+reference+voice |
| execution (TTS) | `GET /execute/tts/voices`, behind `/voices` | Voice picker; empty/unavailable engine → visible state |
| execution (notes) | `/execute/note` via gateway `/api/communication/notes/*` (called by the UI) | Write a verse note to Nextcloud (`Bible/{Title}.md`) |
| execution (chat) | `POST /execute/talk` (`action: post_card`) | Quiz / plan / memory / badge cards into the family room |
| geo | `POST /api/geo/stars` | Bank every award and score into the one ledger |
| identity | `GET /api/internal/activity-sharing` | Consent check before any cross-user read |
| gateway | `POST /v1/chat/completions` | Jarvis study answers |
| telemetry | `POST /api/telemetry/notify` | Reading reminder delivery to the phone |
| storage | `POST /providers/list`, `POST /providers/fetch` | Browse and fetch from the Calibre shelf |
| execution (HA), planned | `POST /execute/ha_service` (`tts.piper`) | "Play on the kitchen speaker" while the family cooks |
| execution (games), planned | gateway `POST /api/communication/talk/game` | In-chat Bible trivia round |
| rag, planned | `POST /rag/ingest`, `POST /rag/search` | Study memory: promote a note to searchable memory; "where did we discuss this?" |
| workspace_runtime, planned | `/files/write`, `/workflow/write-sync-commit` | Opt-in export of journal / memory sets to a family workspace |

The gateway proxies each route the same way: resolve the caller
(`_bible_caller`, built on `_acting_identity`), call upstream with
`X-Internal-Secret`, pass JSON through, and raise with upstream detail on
failure (`_bible_json`).

### Achievements (data-driven, derived)

`services/bible/achievements.json` uses the same `kind`/`rule` schema as
geo's. An excerpt (the file holds 20 badges over 8 rule types:
`chapters_total`, `books_read`, `read_streak`, `plan_days_total`,
`plan_completed`, `memory_mastered`, `quizzes_total`, `quiz_perfect`):

```json
{
  "schemaVersion": 1,
  "kind": "jarvis.bible.achievements",
  "achievements": [
    {
      "id": "first_chapter",
      "name": "In the Word",
      "description": "Finish your first chapter of Scripture.",
      "points": 5,
      "rule": { "type": "chapters_total", "value": 1 }
    },
    {
      "id": "streak_7",
      "name": "A Full Week",
      "points": 25,
      "rule": { "type": "read_streak", "value": 7 }
    }
  ]
}
```

Rules are computed from the `ReadingEvent`, plan, memory and game tables
(`metrics.py`); each unlock banks points into `geo:points:{user}` **first**,
then posts an activity card to the family Talk room via the existing typed
envelope (`announce_awards` pattern: silent unless `FAMILY_CHAT_TOKEN`
names a room, so a deployment without family chat never posts). Awards are
banked on first read so a badge keeps its first-earned date.

### Family sharing

- `UserActivitySharing.data.share` gains the `"bible"` scope
  (alongside `totals`, `workouts`, `achievements`); the existing
  audience/`user_ids` enforcement is reused verbatim. The bible
  service calls Identity `GET /api/internal/activity-sharing` before any
  cross-user read.
- Shared slices when `bible` is opted in: reading totals, current plan +
  progress, streaks, earned badges, mastered memory verses. **Never**
  shared: note/highlight content, quiz answers, Jarvis questions.
- Family plans: following the same plan is the group. `GET /plans/{id}/family`
  projects only members' day progress (no reading content), and only for
  members who share their Bible activity with the viewer.

### Quizzes, memorization, games

**Games** (`services/bible/games.py`): rounds are generated from the
installed text by four question kinds, `which_book`, `fill_blank`,
`book_after` and `chapter_count`, using curated verse references in
`games/verses.json`. Modes: Daily Quiz (date-keyed, the same questions for
every family member, so the leaderboard is fair), Verse Race (60s), Which
Book?, Fill the Gap, What Comes Next? and Books of the Bible. An attempt is
scored on the server by regenerating the round from `(mode, seed, version)`,
so the answer key never reaches the client; the first daily try banks one
geo star per right answer (`reason: "game"`). Planned: Jarvis-authored
questions (the server would validate every answer against the referenced
text and drop the batch with a visible error if validation fails),
`guess_the_verse`, `word_find`, true/false.

**Memorization** (`services/bible/memorize.py`): recall ladder
`read → cloze → first letters → recall → mastered`; each verse has a
`level` and a `due_on` date on the interval ladder 1, 3, 7, 14, 30 days,
then 90 once mastered; a miss drops a level and brings the verse back
tomorrow. Six built-in sets live in `games/memory_sets.json`, and answers are
graded word by word against the corpus on the server. Planned challenge
modes: **timed recall**, **chain** (recite the next verse in a set in
order), **weekly set** (Sunday review of the week's due verses).

### Jarvis study assist

- **Ask panel** (shipped, `components/bible/JarvisStudyThread.tsx`): a
  verse's action sheet has "Ask about this verse" →
  `POST /api/bible/study/ask/stream` with the question, the reference, the
  translation in hand and the depth. The server assembles the prompt from
  **that passage's verses and its own `StudyNote` rows** (the reader's
  question is the only free text) and returns `verses_used` / `notes_used`
  so the panel can say what the answer was built from. Turns are kept as a
  thread, so a follow-up keeps the context. Each question logs
  `assistant_ask` with the reference and **not** the question, so what a
  reader asked never leaves the reader's own history.
- **Three depths (child / teen / adult)** change only the wording of the
  answer; the passage and the rules stay the same. **Reference chips** show
  only references the server's own parser accepts (at most 8). Tapping one
  (or Enter/Space on it) opens a **reference popover** (`ReferenceChip.tsx`)
  with the passage text, fetched from `GET /passages` in the translation
  being studied only when the chip is opened. The popover shows at most 7
  verses (BLB ScriptTagger's cap); a longer passage says "Showing 7 of N
  verses" with a "More on Blue Letter Bible" link the server builds
  (`GET /blb/link`). Actions: **Open in reader** (only where there is a
  reader to open) and **Copy** (`Reference (VERSION)` + the text; a refused
  clipboard says why). Loading, a failed fetch (verbatim, with a retry) and a
  passage with no imported text each have their own state. It is a bottom
  sheet on a phone and sits beside the chip from `md` up; either way it is a
  modal dialog: focus moves in, Tab is trapped, Escape or a tap outside
  closes it, and focus returns to the chip.
- **Over-long input is refused rather than truncated**: an empty question,
  one over 2,000 characters, or a passage over 150 verses is a 400 naming
  the number, because a silently shortened passage would produce a confident
  answer about different words. Notes are capped instead: the first 40, each
  cut at 1,200 characters.
- **Still planned** (phase 7, not built): the intent engine routing "read
  john 3" / "quiz me on the beatitudes" / "explain like I'm 5" into the
  matching feature, and Jarvis *authoring* study aids (cross-reference lists,
  plan-day discussion questions, a quiz from any passage) with the server
  validating every generated answer key against the corpus before it is
  offered.
- **Verse of the Day context** (shipped): press and hold the verse card (or
  right-click it) and choose **Get context**, **Read passage** or **Copy
  verse**. *Get context* opens a panel backed by
  `POST /api/bible/verse-of-day/context`: the bible service asks Jarvis for two
  or three plain-language paragraphs on who is speaking, what surrounds the
  verse and where it sits in the chapter, built from the chapter (±8 verses)
  and the installed study notes. The answer is cached per translation+verse,
  so every surface showing that verse gets it from `/verse-of-day` and
  `/daily` without calling the model again. A background pass fills today's
  missing note at startup and every 30 minutes, retrying quickly if the first
  pass starts before the gateway is ready; the panel's refresh button
  regenerates it, and a failed refresh leaves the previous note readable.
- **Fail-fast**: no gateway URL, an HTTP error, a non-JSON body, an
  unreachable model or an empty reply each become a named 503 carrying the
  upstream's own sentence. The panel shows it verbatim and keeps the question
  in the box, never a canned answer.

## UI / UX architecture

### Information architecture

Route **`/bible`** (`src/pages/Bible.tsx`), registered in `App.tsx` inside
`ProtectedRoute`, with the nav entry **Bible** defined once in
`components/layout/navItems.ts` (used by the Sidebar, the BottomNav and the
Header search). Admins also get **Admin › Bible** at `/admin/bible`. API
methods are in `services/api.ts`, types in `types/api.ts`.

Inside the page, one row of **section tabs**:
**Read · Today · Play · Memorize · Progress** (`?tab=` selects one;
`?tab=devotional` opens Today). On a phone the row fills the width with the
icon above the label and tall touch targets; from `sm` up the icon sits
beside the label. Section components live under `src/components/bible/`.
Plans and the devotional are on Today; study help (notes, Ask, BLB,
interlinear) opens from a verse's action sheet.

### Reading parity with the Bible App (Phase 1 acceptance checklist)

This is the definition of done for the reader. Every row is a checkbox on
Phase 1, not a nice-to-have. If a row can't be shipped, the row moves out
of Phase 1 explicitly rather than being quietly dropped.

| Must-have (Bible App parity) | How we meet it | Phase |
|---|---|---|
| **Opens where you left off**: cold start lands on the last verse you read | `last_book`/`last_chapter`/`last_verse` in `UserBibleState`, saved with `PUT /state`; "Continue reading" is the Read landing card and the default route for `/bible` | 1 |
| **Text you can actually read**: font size, line height, serif/sans, dark & light | `font_scale`, `line_height`, `theme`, `page` persisted per user; typographic scale tuned for long-form (≈65ch measure, 1.6 leading, verse numbers de-emphasized) | 1 |
| **Tap anything**: verses are targets, not text | Verse chips ≥44px row hit area on `pointer-coarse`; whole verse row tappable; tap-hold opens the action sheet on touch (no hover-only path) | 1 |
| **Highlights + bookmarks in color** | `VerseMark` with 5 to 8 colors, per-chapter color rail, "highlights in this chapter" strip | 1 |
| **Notes on a verse** | Inline note editor; body written to **Nextcloud via the notes handler** (`Bible/{Title}.md`), index row only in the bible service | 1 |
| **Marks sync across devices** | Marks live in the bible service (server-side), not local storage, so phone + desktop agree; cached for offline | 1 |
| **Chapter/book navigation** | Book grid (66 books), chapter grid, "next/previous chapter" swipe + buttons, "go to date" for plans | 1 |
| **Search that finds verses** | Local corpus search (word, phrase, reference, `"John 3:16"`, ranges `John 3:16-21`, multi-passage `John 3:16, 4:22`); BLB `preSearch` deep link for the long tail | 1 |
| **Read aloud** | bible `/narration*` via `/api/bible/narration*`; play/pause, voice picker, 0.75x-1.5x speed control, per-verse highlight while speaking, and a speaker picker that sends the passage through the authenticated announcement path (`BibleReadAloud.tsx`); audio cached in `NarrationAudio` on the server and per session in the client | 1 |
| **Verse of the Day** | Deterministic by date, at the top of Today and in the `bible_daily` widget | 1 |
| **Share a verse** | Verse image card (`src/lib/verseImage.ts`) → native share sheet or web share; share to Family chat; deep link back to the passage | 1 |
| **Compare / parallel versions** | `compare_version` picks a second translation, fetched from `/passages` like any other. `ChapterReader` puts the two in one row per verse from `sm` up and stacks them on a phone, since a phone cannot show two readable columns. A verse the second translation does not have says "Not in this translation." rather than vanishing: NIV2011 genuinely omits Matthew 17:21, and an invisible gap reads as a rendering fault. The version picker never offers the translation being read, and the server refuses it too. **Panes** (`split_view = parallel`) shows each translation as its own whole chapter in a scrolling pane, side by side from `sm` up and stacked on a phone; while linked, scrolling either pane keeps the other on the same verse (see *Synced scroll* below), and a "Scrolling together / separately" toggle unlinks them. A comparison that fails to load says so with a retry, in either layout | 1 |
| **Commentary beside the text** | `show_notes` puts the chapter's study material in a column next to the reader on `lg` and under the chapter on a phone; off by default, because the reader starts as text. The notes are already fetched to mark which verses carry study material, so this costs no extra request. Each note names its verse and tapping it moves the reader there. The commentary is never mixed into `BibleVerse.text` | 1 |
| **Streak on screen** | App-open streak (offline-tolerant) and Guided-Scripture streak (resets on a skipped day, number hidden until earned again): YouVersion's two-streak model, not one generic counter | 1 |
| **No network? Still read** | Last 120 passages plus the last `/state`, `/versions` and `/books` answers kept in `localStorage` (`src/lib/bibleOffline.ts`); reads never wait on an upstream | 1 |
| Offline audio download | Recorded audio is out of scope (licensed). Documented as a deliberate gap, not a bug | later |

Deliberately **not** in Phase 1 (and why): 1,200+ versions (needs
licensed API keys; the picker shows PD versions offline and unlocks more
only when `bible_api_key` is configured), recorded audio (licensed), verse
images with photo backgrounds (camera-roll import is a later polish item).

### Screen specs

These began as the plan. File names below are the ones in the tree; items
marked *planned* are not built.

**1. Reader** (`components/bible/ChapterReader.tsx`, `ReferenceBar.tsx`): *the product*

- **Landing**: "Continue reading" (book, chapter, verse) is the default
  state of `/bible`. Everything else lives in the other tabs.
- **The text**: verse numbers as tappable chips (≥44px row hit area on
  `pointer-coarse`); whole verse row tappable; the chapter's bookmarks and
  highlights shown on their verses. *Planned:* red-letter styling, a
  position rail, a per-chapter marks strip.
- **Reference bar** (always one tap away, never a modal maze): reference
  input, book/chapter steppers, translation, study Bible, compare and
  display settings. On a phone it collapses to a one-line passage header with
  previous/next, and every other control opens in a sheet.
- **Verse action sheet** (`VerseActionSheet.tsx`, tap / tap-hold):
  **Highlight** (colors), **Bookmark**, **Note** (inline editor → Nextcloud,
  then the verse is marked as having one), **Copy**, **Share** (system share
  sheet, verse image, or Family chat via `ShareToChatSheet.tsx`), **Study
  notes**, **Interlinear** (`InterlinearPanel.tsx`), **Ask about this
  verse** (Jarvis, grounded in this passage), **Study at BLB** (deep link),
  **Memorise this verse**. *Planned:* Add to plan day.
- **Your own notes reach back into the text** (shipped): a verse with a
  written note carries its own glyph (`bible-verse-noted-...`, announced as
  "Has your note"), and the action sheet offers **Open your note**, which
  reads the body back from Nextcloud by the path the note handler reported.
  If the server did not report a path, the verse is **not** marked and the
  sheet says so; pointing a reader at a filename the client guessed would
  be worse than showing nothing.
- **Focus mode** (shipped, `components/bible/FocusReader.tsx`): a full-screen
  reading page reachable from the reader header. It is a *way of looking at
  the chapter*, not a second copy of it (the same verses, marks and
  previous/next actions), so nothing can drift. Chrome fades out on a tap and
  back in on the next, and the page tint follows the hour
  (`components/bible/focusAmbience.ts`, a pure `ambienceFor(hour)` kept in its
  own module so the component file exports only a component).
  The app's bottom bar stays over the page, so the text ends exactly clear of
  it (`pb-clear-bottom-bar`): the bar publishes its measured height, gesture
  area included, as `--bottom-bar` (`lib/bottomBar.ts`). A fixed padding was
  shorter than the bar on phones with a tall gesture area, which hid the
  last lines and stopped scrolling early.
- **Study notes** (`components/bible/BibleStudyNotes.tsx`): the sheet's
  "Study notes" row opens a panel that asks `/study/notes` for *that verse*
  in *that translation*. Verses carrying material are marked in the reader
  with a small bookmark glyph (and announced to screen readers as "Has
  study notes") so a study Bible announces itself without ever mixing a
  commentary into the text. The panel offers one filter per kind the
  installed translation actually carries, with counts
  (`Commentary · 12055`), and hiding a kind is a toggle rather than a
  reload. Chapter-level material (introductions, headings) is shown once
  above the verse notes. When the translation carries none (KJV, ASV,
  WEB), the panel says so in one sentence pointing at **Admin › Bible**,
  rather than showing an empty box or naming a command a reader cannot run.
  It names the study Bible it actually loaded, and
  offers the two choices the reader owns:

  - **Study Bible**: chips appear only when more than one edition is
    installed for the translation, each labelled with its note count
    (`The MacArthur Study Bible · 9,800`). Tapping one refetches, and the
    choice is saved as `default_edition` so the next device opens the same
    commentary. The reference bar carries the same picker next to the
    translation, because that is where someone looks for it.
  - **Notes from other translations**: an unchecked checkbox naming the
    study Bibles it *would* add ("NKJV, Life Application Study Bible,
    New International Version"). Once on, every note carries a
    translation + study Bible badge and notes group by study Bible before by
    verse, so a reader comparing two commentaries reads one at a time
    instead of alternating per verse.

  Neither choice is a server opinion: the server resolves them and reports
  what it resolved (`edition`, `edition_name`), and the reader's choice is
  what gets remembered.
- **Version selector**: the corpus catalogue first. A translation that is
  catalogued but not installed appears greyed with the reason; api.bible
  versions appear only when `bible_api_key` is configured, otherwise a
  visible "configure bible_api_key to unlock more versions" item (fail-fast,
  never a silent 3-version ceiling). If a saved translation is not
  installed, reading continues in the best installed one *and* a banner says
  which was missing.
- **Read aloud** (`BibleReadAloud.tsx`): the passage on screen is narrated
  piece by piece from `/narration/plan` and `/narration/chunk`; play/pause,
  voice picker from `/voices`, `read_aloud_voice` persisted. **If no voice
  engine/voices are installed** the button is present but the panel states
  exactly why (`FileNotFoundError: Kokoro voices missing`) and how to fix
  it, never a button that does nothing. *Planned:* spoken-verse highlight,
  speed control, the HA `tts.piper` kitchen speaker.
- **Compare**: the second translation in a row per verse from `sm` up,
  stacked on a phone (**Rows**), or as two whole chapters in their own
  scrolling panes (**Panes**).
  - **Synced scroll** (`ParallelReader.tsx`, `verseAnchor.ts`): the panes are
    matched by *verse*, not by scroll ratio, because two translations are
    never the same height. The verse straddling the top of the pane being
    scrolled, and how far into it the reader is, is put at the top of the
    other pane; a verse the other translation lacks (NIV2011's Matthew
    17:21) lands at the end of the verse before it. Only the pane the reader
    is driving (last touched, wheeled, clicked or focused) moves the other,
    so the programmatic scroll never echoes back. Relinking, or the second
    translation arriving, lines it up with the reader's place. Each pane is a
    focusable region, so arrow keys and Page Down scroll it from the
    keyboard.
  - *Planned:* per-pane font control, a "diff" highlight for verses that
    differ textually.
- **Reading position** saved with `PUT /state`; `chapter_read` +
  `chapter_complete` events recorded server-side so the streak and stats
  are not client-reported.

**2. Today** (`components/bible/BibleToday.tsx`, `BiblePlans.tsx`)

- Verse of the Day and today's devotional.
- Imported devotionals: the **Devotional books** shelf lists every PDF/EPUB
  imported as *Daily Devotions*. A book's card switches it between daily and
  weekly reading (weekly steps once a week, so 21 Oct reads devotion 42) and
  opens it in the focused reader. Each reader's **default devotional** is
  saved with their Bible preferences: the "Devotional book" selector on Today
  remembers the pick and opens on that book's reading for the day, while
  "Default sources" keeps the built-in readings.
- Plans: catalog grouped by category; a plan shows its day checklist,
  progress, **Catch me up** (marks missed days complete in one tap,
  YouVersion parity) and stop (asks first). Twelve plans are generated from
  the canonical chapter table (`games/plans.json`).
- **Reading along**: family members on the same plan, consent-gated
  (`GET /plans/{id}/family`). *Planned:* per-day discussion cards in the
  family Talk room.

**3. Study** (from the verse action sheet)

- **Jarvis thread** scoped to the passage (`JarvisStudyThread.tsx`): a
  thread of turns, each answer built only from the passage and its study
  notes, with a provenance line (`Read 1 verse and 3 notes`), streamed
  answers, reference chips and a depth selector (child/teen/adult).
  *Planned:* "Generate quiz", "Suggest memory verse", "Discussion
  questions".
- **BLB and interlinear**: per-verse BLB and interlinear links ship today.
  *Planned:* a Strong's/lexicon/concordance/dictionary tools row (new tab on
  desktop, in-app browser on native).

**4. Memorize** (`components/bible/BibleMemorize.tsx`)

- Built-in sets and the reader's own verses, with level and due date.
- **Recall flow**: `read → cloze → first letters → recall`, graded on the
  server word by word, driving the SRS ladder.
- Due queue ("3 verses due today"). *Planned:* timed recall, verse chain,
  weekly review.

**5. Play** (`components/bible/BibleGames.tsx`)

- Hub cards for the modes in §"Quizzes, memorization, games": Daily Quiz
  (date-keyed, family leaderboard), Verse Race, Which Book?, Fill the Gap,
  What Comes Next?, Books of the Bible.
- One question at a time with large option buttons, then a result with the
  score and stars earned. Scores bank to the shared points ledger.

**6. Progress** (`components/bible/BibleProgress.tsx`)

- Usage stats and streaks (app-open, reading).
- **Achievements**: earned badges and next-up with `4/7 days` progress,
  following the `AchievementsPanel` patterns
  (`components/wander/AchievementsPanel.tsx`).
- **Sharing**: the `bible` scope lives in `ActivitySharingPanel` with the
  other activity scopes (audience: circle / specific users).

### Dashboard widget: `bible_daily`

Registered in `stores/widgetStore.ts` (`defaultWidgetDefs`: key
`bible_daily`, label "Verse of the Day", `minSize: 'small'`,
`defaultSize: 'medium'`), component `components/widgets/BibleDailyWidget.tsx`.
One `/api/bible/daily` call fills the whole card: verse, devotional, streaks
and the plan line. `small` drops the devotional and the streak to keep the
verse readable, `medium` shows both, and anything taller scrolls. Tapping the
verse opens `/bible?ref=…`; the devotional opens `/bible?tab=devotional`.

### Mobile parity checklist (CONTRIBUTING.md, applied)

- 44px touch targets: interactive rows, option buttons, steppers; desktop
  density kept via `pointer-coarse:min-h-11`.
- No hover-only affordances; no `onMouseEnter`-only paths (the reference
  popover opens on a tap, as a bottom sheet on a phone).
- Phone grid fit: widget content scales with container (SVG `viewBox`,
  no fixed pixel canvas); page sections fit 280px/200px bands.
- `safe-area-*` insets on sticky bars and composers; content that the
  bottom bar can cover ends with `pb-clear-bottom-bar`, a full-screen layer
  above the bar with `pb-clear-system-bar` (`index.css`), never a guessed
  number; `text-base` inputs (no iOS zoom); single-column stacking on phones.
- Native branches via `Capacitor.isNativePlatform()`: share sheet,
  in-app browser for BLB links, haptics gating; `useHaptics()` on streak,
  correct answer, level-up, achievement unlock.
- Offline tolerance: last chapter, `/state`, `/versions` and `/books` kept
  on the device; app-open streak counts offline and syncs on reconnect
  (YouVersion parity).
- Themed (light/dark + theme packs), serif option for Scripture.

### Component inventory

Built:

```
src/pages/Bible.tsx                               # tabs, Read section, routing by ?ref= / ?tab=
src/components/bible/ChapterReader.tsx            # verses, marks, compare column, chapter notes
src/components/bible/ParallelReader.tsx           # two translations in panes, verse-synced scroll
src/components/bible/verseAnchor.ts               # verse-anchored scroll arithmetic (pure)
src/components/bible/ReferenceBar.tsx             # ref input, book/chapter steppers,
                                                  #   version, study Bible, compare, display;
                                                  #   one-line header + sheet on a phone
src/components/bible/BookPicker.tsx               # 66-book grid + chapter grid
src/components/bible/VerseActionSheet.tsx         # highlight, bookmark, note, copy, share,
                                                  #   study notes, interlinear, ask, BLB, memorise
src/components/bible/verseMarks.ts                # match marks to verses on book/chapter/verse
src/components/bible/ShareToChatSheet.tsx         # share a verse into a Family chat
src/components/bible/ChapterNotes.tsx             # commentary beside the text, per verse
src/components/bible/BibleStudyNotes.tsx          # commentary/footnotes per verse,
                                                  #   study-Bible picker + cross-version
src/components/bible/InterlinearPanel.tsx         # imported word-by-word interlinear
src/components/bible/JarvisStudyThread.tsx        # ask about this passage: question,
                                                  #   thread, provenance, failures verbatim
src/components/bible/ReferenceChip.tsx            # reference chip + passage popover
                                                  #   (7-verse cap, open in reader, copy)
src/components/bible/BibleReadAloud.tsx           # narration player + voice picker
src/components/bible/FocusReader.tsx              # full-screen reading page
src/components/bible/focusAmbience.ts             # hour -> page tint (pure function)
src/components/bible/readingPage.ts               # reading-page tones
src/components/bible/BibleToday.tsx               # verse of the day + today's devotional
src/components/bible/BiblePlans.tsx               # plans, catch-up, reading along
src/components/bible/BibleMemorize.tsx            # memory verses and recall
src/components/bible/BibleGames.tsx               # Play tab
src/components/bible/BibleProgress.tsx            # stats, streaks, badges
src/lib/bibleOffline.ts                           # offline copies (withOfflineCopy)
src/lib/verseShare.ts                             # a verse as a chat line
src/lib/verseImage.ts                             # verse share image
src/components/widgets/BibleDailyWidget.tsx
src/components/admin/BibleAdminPanel.tsx          # Admin › Bible: catalogue,
                                                  #   providers, upload/path import,
                                                  #   import history, cost estimate
                                                  #   before Install, one-book test
```

Planned and not built: a family plan board, a study-memory search over RAG,
and an opt-in workspace export.

Tests: `src/pages/Bible.test.tsx` (including comparing two translations: the
picker never offers the translation being read, the second column renders, a
verse the second translation lacks says so, and turning the comparison off
leaves the reader's own text alone; switching to **Panes** saving
`split_view` and showing both whole chapters; a comparison that fails to load
saying so; and the Focus button opening the full-screen page),
`src/components/bible/ParallelReader.test.tsx` (the other pane following by
verse rather than ratio, a missing verse landing at the end of the one before,
either pane driving, unlinking and relinking, and the loading/failed/empty
comparison states) and `verseAnchor.test.ts`,
`src/components/bible/FocusReader.test.tsx` (chrome fades on a tap, prev/next,
the loading line rather than a blank page, a highlight tinting its own verse
and not its neighbour, and `ambienceFor` being a pure function of the hour),
`src/test/ReferenceBarPhone.test.tsx` (the one-line phone header, the full
controls absent until the sheet is opened, choosing a chapter closing it, and
jsdom's default landing on the desktop bar),
`src/components/bible/BibleStudyNotes.test.tsx` (including the note pointer
using the path the server reported, and no pointer at all when none was
reported), `src/components/bible/JarvisStudyThread.test.tsx` (a chip
showing its passage before opening the reader),
`src/components/bible/ReferenceChip.test.tsx` (nothing fetched until tapped,
loading then a failure verbatim with a retry, the empty passage, the 7-verse
cap with the BLB link, open in reader, copy and a refused copy, keyboard open
with Tab trapped and Escape returning focus to the chip, and a tap outside
closing it),
`BibleToday.test.tsx`, `BiblePlans.test.tsx`, `BibleMemorize.test.tsx`,
`BibleGames.test.tsx`, `BookPicker.test.tsx`, `verseMarks.test.ts`,
`VerseActionSheet.links.test.tsx`,
`services/bible/tests/test_bible_study_ask.py` (prompt assembly, every refusal
by name, `_extract_answer` shapes, and a parametrised "no failure becomes an
answer"),
`services/bible/tests/test_bible_editions.py` (two study Bibles over one
translation, cross-version gating, re-import scoping, recounting a stale note
cache),
`src/components/widgets/BibleDailyWidget.test.tsx` (vitest), and the
service tests in `services/bible/tests/` (achievements, reference resolver,
PDF and EPUB extraction, study-note joining, games, memorization, plans,
reminders, sharing enforcement, narration, BLB links and interlinear,
startup with an unconfigured upstream).

Read-aloud: `src/components/bible/BibleReadAloud.test.tsx` plays the returned
bytes through a `data:`/blob URL and asserts the visible "no voices installed"
and "passage too long" states rather than a silent no-op.

Imports: `src/components/admin/BibleAdminPanel.test.tsx` covers the catalogue
with its primary marker, an unconfigured provider naming `bible_api_key`,
installing from a provider, from a server path and from an upload, a refusal
shown in full, and the retry when the catalogue cannot be read, plus the cost
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
| `BIBLE_DATABASE_URL` | `.env` → SQLite file under `/data` (compose passes `sqlite:////data/bible.db` when `.env` leaves it out) | Every CLI and the service refuse to start with a message naming the setting (no default path in code) |
| `BIBLE_DEVOTIONAL_DIR` | `.env` / `bible_devotional_dir` `GlobalSetting` | Local devotional source is skipped **with a named reason**; the day still resolves through the next source, and `/devotional` reports every source that was skipped and why |
| `bible_api_key` (`BIBLE_API_KEY`) | Identity `GlobalSetting` / env | Enter it in Admin › global settings (the field is masked because the key contains `api_key`) or set `BIBLE_API_KEY`. The bible service reads it **from Identity at call time**, so a key saved in the app works without a restart; a blank setting never erases one the environment supplied. The api.bible provider is still **listed** on Admin › Bible with its reason naming this setting rather than hidden |
| `BIBLE_IMPORT_DIR` (`bible_import_dir`) | `.env` / `GlobalSetting` | Admin › Bible says nothing can be installed yet and why; reading is unaffected. A file import refuses with that sentence rather than picking a path |
| `BIBLE_BOOKS_PATH` (`bible_books_path`) | `.env` / `GlobalSetting`, seeded `/Books` | Admin › Bible browses this Nextcloud folder for devotional/study PDFs and EPUBs. A missing folder is named in the panel; Calibre uses `calibre_library_path` instead |
| `BIBLE_PROVIDER_CACHE` (`bible_provider_cache`) | `.env` / `GlobalSetting` | Where fetched provider chapters are kept write-once. Blank derives `<directory of BIBLE_DATABASE_URL>/provider-cache`. Left unset, every chapter is fetched again on the next import; Admin › Bible says so on the cost line rather than importing quietly uncached |
| `BIBLE_PROVIDER_CALL_BUDGET` (`bible_provider_call_budget`) | `.env` / `GlobalSetting` | The most **new** requests one provider import may need. Blank means no ceiling. A run over it is refused before the first request, naming both numbers; only uncached chapters count, so a warm cache costs nothing |
| `CALIBRE_LIBRARY_PATH` (`calibre_library_path`) | `.env` / `GlobalSetting`, **the same setting the storage service's Calibre RAG index uses**, so a server already indexing books gets Bible imports free | Unset, Admin › Bible's Browse button is disabled and says so by name. **Not** a guess: a guessed shelf reports the wrong folder as an empty library, which is worse than saying nothing. The bible service reads it from Identity **per call**, so pointing it at a shelf takes effect without a restart (the storage index reads it through `resolve_runtime_config()`, which needs one) |
| `STORAGE_SVC_URL` | `.env` (compose sets it from `BRIDGE_STORAGE_SVC_URL`) | The shelf browse and fetch routes return "storage_svc_url is not configured", naming the setting; the bible service never holds Nextcloud credentials itself |
| `blb_base_url` (`BLB_BASE_URL`) | Identity `GlobalSetting` / env; seeded **blank on purpose**, with no default host | BLB links return the server's "blb_base_url is not configured" message in the action sheet |
| TTS voices (`/execute/tts/voices`) | existing execution service (`DEFAULT_TTS_VOICE`, Kokoro/Edge) | Reader states the real engine error (e.g. `Kokoro voices missing`) and how to install; never plays nothing |
| LLM provider (`assistant_model` etc.) | existing config | Ask Jarvis shows the standard unconfigured message |
| `FAMILY_CHAT_TOKEN` | existing | Achievement/plan cards stay silent (existing pattern) |
| Notes storage (`nextcloud` \| `local`) | existing notes config | Falls back to `LOCAL_NOTES_ROOT` (existing behavior); the reader shows which store it wrote to |

### `.env` is a seed; the config DB is the runtime

`.env` is read **once**, at startup, and only ever seeds. After that the
Identity `GlobalSetting` table is the runtime source of truth, editable from
Admin › Settings: no restart, no editing files on the host.

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
| `bible_api_key` | Read from Identity **per call** by `main._live_settings()`. A key pasted into Admin takes effect immediately instead of needing a restart, and rotating a revoked key takes effect just as fast. Blank saved never erases an env-supplied key. |
| `BIBLE_PROVIDER_CALL_BUDGET` | Coerced with `int()` at the point of use (`provider_cache.call_budget()`). A malformed value (`12oo`, `0`, `-5`) is an **error naming the setting**, not a silently absent ceiling. |

### Getting `.env` onto a new host

`scripts/deploy_remote.sh` pipes the local `.env` over ssh into
`scripts/merge_env.py`, which **appends only what the host is missing**:

- a key already defined on the host is **preserved**, never rewritten;
- the host's file is copied to `.env.bak` first, but only when one existed,
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
  lexicon/commentary/site text is copyrighted, never scraped or
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
  on the default read path: the provider is reached only at import time, at
  `api.scripture.api.bible` (the `api.bible` host 404s every `/v1/*` path).
- All share cards render our own text (no third-party artwork).

## Books, study sessions and chapter questions

Ordinary PDF/EPUB books are imported separately from Bible translations:

- **Admin › Bible › Devotionals and study books** browses two shelves through
  the storage service: the Nextcloud books folder (`bible_books_path`, seeded
  `/Books`) and the Calibre library (`calibre_library_path`, read as raw
  files). A file can also be uploaded from the admin's computer, which covers
  a folder such as `~/Desktop/Bible Study`.
- A book is stored as `ImportedBook` plus ordered `ImportedBookChapter` rows;
  it is never checked against the 66-book canon. EPUB uses the spine-aware
  extractor; PDF uses one page per section because a scanned or typeset page
  is the natural reading unit.
- Books marked **Daily Devotions** become a `book:<id>` work. The rotation
  starts on Day 1 of the year at the book's first printed devotion — front
  matter (contents, introduction) never takes a day — and each book carries
  a Daily or Weekly cadence. Weekly steps once a week, so Oct 21 (week 42)
  reads devotion 42. The Devotional books shelf on Bible › Today shows each
  imported devotional, lets you switch its cadence, and opens it in the
  focused reader.
- Books marked **Bible study** are readable under Bible › Studies. Lines that
  end in a question in the last third of a chapter are stored as its
  end-of-chapter questions. "Answer from this chapter" calls the model with
  that chapter as the only source and keeps the answer in the study session.
- A Bible study book opens in a focused reader rather than a wall of page
  buttons: one page at a time, a contents sheet, progress remembered per book,
  and the Scripture reader's text size, spacing, tone and serif/sans settings.
  Each page can be handed to Jarvis (grounded on that page), saved to Notes as
  a `Bible` note, or shared to a family chat.
- **Studies** is a first-class tab. `StudySession` rows keep several passages
  open at once; `StudySessionTurn` stores each grounded answer. Opening a new
  passage no longer replaces the previous conversation.
- The Notes page remains the Google Keep-style surface over Nextcloud
  markdown: pins, colours, checklists, search, and the shared editor. Bible
  note marks still point at those notes rather than creating a second store.

## Phased implementation

Reading is the gate. Phase 1 is not "a slice of the Bible feature", it is
the reader, and it is not declared done until every row of §"Reading
parity with the Bible App" passes on desktop **and** phone.

| Phase | Scope | Definition of done |
|---|---|---|
| **0** Config + corpus + reading + study (done) | `services/bible/` service (port 8010), compose + Caddy + `BRIDGE_BIBLE_SVC_URL` + Identity `GlobalSetting`s, reference resolver, `corpus_manifest.json`, PDF + EPUB importers, `StudyNote` table with the two-axis `BibleEdition` corpus, `migrations.py` for existing databases, `editions.py` CLI, gateway `/api/bible/*`, `/books`, `/versions`, `/passages`, `/search`, `/editions`, `/study/notes`, `/study/ask`, `/verse-of-day`, `/devotional`, `/marks`, `/state`, `/events`, `/stats`, `/streaks`, `/achievements`, `/activity/*`, `/blb/link`, `/daily`, `/narration`, `/voices`, `/admin/imports*`; `/bible` page, `bible_daily` dashboard widget, Android Verse-of-the-Day home-screen widget, `bible` sharing scope, Admin › Bible import surface, one primary translation, long-term per-chapter provider cache with cost estimate, request budget and one-book dry run; **the mobile reference sheet** (one-line passage header with prev/next, every other control in a sheet), **the `note` mark kind** with a verse-note glyph and read-back from Nextcloud, **Ask about this verse** (grounded Jarvis answers), **Focus mode** with hour-tinted chrome, `favorite_version`/`compare_version`/`show_notes` preferences; `bible` added to the image CI matrix | **Shipped.** Service boots in compose; corpus imported (KJV/ASV/WEB + NKJV with 44k study notes under the Nelson study Bible); multiple study Bibles per translation and opt-in notes from other translations both work end to end; every unconfigured path returns a message naming the setting; proxy is caller-scoped with the family 404 intact. Live-verified: `/study/ask` answered a John 3:16 question in 83s citing 1 verse and 3 notes |
| **1** **The reader** (mostly done) | `BookPicker`, compare view, HA speaker playback (read-aloud itself ships in Phase 0 via `BibleReadAloud.tsx`), note editor → Nextcloud, Continue reading card, verse image share, reference popover, offline cache. **Shipped (2026-10-13):** opaque reading page in dark/sepia/light tones (AAA contrast), 66-book + chapter grid picker, swipe to turn the chapter, last 120 passages kept on the device for offline reading, verse image share card, Continue reading card. **Shipped (2026-10-20):** per-verse highlight while speaking and sending the reading to a chosen speaker. **Shipped (2026-10-24):** Panes comparison with verse-synced scroll, and the reference-chip popover | A user opens the app and is reading, where they left off, on desktop and phone; marks/notes/position sync; chapters read offline; read-aloud speaks a chapter and states its errors honestly |
| **2** Plans + daily rhythm (plans done) | **Shipped (2026-10-13):** `services/bible/plans.py` (twelve plans in `games/plans.json` generated from the canonical chapter table, every chapter exactly once, side-by-side tracks), `PlanEnrollment` table, `/plans*` (start, complete/undo a day, catch me up, stop), plans on the Today tab, plan badges live. Plan line on the dashboard widget and a daily reading reminder (bible-service loop + telemetry notify with a per-day `once` key, the leave-by pattern; automation has no timer-create API and its daily recurrence drifts across DST) also shipped. Reading along: family members on the same plan, consent-gated | Completing a plan day advances the plan and the badges (tests); missed days are catchable (test) |
| **3** Achievements + points | Rules engine already shipped in Phase 0 (reading/streak rules); plan, memory and quiz rules land with those features; banking to `POST /api/geo/stars`, Talk announcement cards, `AchievementsPanel` reuse in Progress | Unlock → points land in the one ledger, `earned_on` is stable across re-reads, family card posts when configured |
| **4** Family sharing (mostly done) | Family plan board, per-day discussion cards in Talk, in-chat Bible trivia; the `bible` sharing scope already ships in Phase 0. **Shipped (2026-10-13):** consent now reads Identity's real `{users:[...]}` payload (it was always false before), reading along on a plan (no group table: following the same plan is the group), family chat cards for a daily quiz score, a finished plan and a mastered verse (sharers only), badge cards fixed to an accepted `card_kind`. Not yet: in-chat trivia (the Talk game API is unwired in the UI and off unless `FAMILY_GAMES_ENABLED`) | Non-consented user gets 404; feed omits; family plan board and Talk round work for members only |
| **5** Memorization (done) | **Shipped (2026-10-13):** `services/bible/memorize.py` (ladder read → cloze → first letters → recall → mastered, intervals 1/3/7/14/30 days then 90, a miss drops a level), `MemoryVerse` table, six built-in sets (`games/memory_sets.json`), word-level server grading against the corpus, `/memorize*` routes, Bible › Memorize tab, "Memorise this verse" in the verse sheet, `memory_mastered` badges live. Not yet: timed/chain challenge modes, widget line | Recall session advances levels; due queue respects intervals (tests) |
| **6** Quizzes + games (first slice done) | **Shipped (2026-10-13):** `services/bible/games.py` generators built from the installed text (`which_book`, `fill_blank`, `book_after`, `chapter_count`; curated verse refs in `games/verses.json`), modes Daily Quiz / Verse Race (60s) / Which Book? / Fill the Gap / What Comes Next? / Books of the Bible, `GameScore` table, server-side scoring by regenerating from `(mode, seed, version)`, first daily try banks one geo star per right answer, consent-gated family leaderboard, quiz badges now live (`quizzes_total`, `quiz_perfect`), Bible › Play tab. **Still to do:** Jarvis-authored (validated) questions, `guess_the_verse`, `word_find`, true/false, Talk leaderboard cards, in-chat trivia round | Daily quiz identical per family member (test); the answer key never reaches the client (test); scores bank to geo (test) |
| **7** Jarvis study (part done) | Streaming ask, reference chips, depth selector (child/teen/adult), intent routing, study-aid generation with server-validated answer keys, **study memory** (RAG ingest with citable ids + a study-memory search), **workspace export** (opt-in). *(The grounded ask panel ships in Phase 0; this phase adds streaming, chips and the rest.)* **Shipped:** depth selector (answer pitched for a child, teen or adult; rules unchanged), reference chips (only references the server parser accepts), streaming answers (`POST /study/ask/stream`), and a study-memory search over saved session turns (`/study/sessions/search`). RAG ingest of promoted notes and plan reflections (2026-10-24). Not yet: workspace export, study-aid generation | Ask a question on a passage → streaming answer with chips; "where did we discuss this?" returns prior family notes with citations; export writes to the workspace when enabled |
| **8** Polish | Verse images with camera-roll backgrounds, widget sizing pass, Android shortcuts, offline audio cache warming, quiet hours | Native share sheet verified; quiet hours honored |

Each phase: co-located service tests + UI vitest + mobile layout check
before "done" (see CONTRIBUTING.md). Phases 0-1 ship the reading product
(this is what makes it a Bible *reader*); 2-4 the tracking/achievement/family
layer that makes it "A LOT like the Bible App"; 5-6 the games; 7 the
Jarvis learning layer; 8 polish. Phases 2-8 are optional to the reader:
each must be additive and must never regress Phase 1 (regression tests in
Phase 1 guard this).

## Risks / open questions

1. **Corpus provenance**: every row in `corpus_manifest.json` records a
   rights holder, a `license_class` and, for public-domain downloads, a
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
   study EPUB, but streaming it needs byte-range serving and per-verse
   indexing, which is a real project. Read-aloud ships first on the
   existing Kokoro/Edge TTS engine (free, already deployed); open question
   is whether Kokoro is good enough for long-form *listening* or is only
   acceptable as an accessibility/eyes-free aid.
7. **Typographic quality on long text**: Scripture is long-form reading,
   not a feed. Needs real typographic care (measure, leading, verse-number
   hierarchy, widow control). This is the single most visible quality bar
   in the whole feature: budget design time, not just engineering time.
8. **Kids mode**: YouVersion has a separate kids app; our first slice is
   the single Bible experience with child-depth Jarvis explanations. A
   kid profile (simplified UI, only-age-appropriate games) is a candidate
   follow-up, not v1.
9. **Reminders**: the reminder fires at the reader's own `reminder_time`
   (server local time, `TZ`), not a fixed 7pm; open items are quiet hours
   and making dismissal sticky.
10. **Reading-event volume**: chapter reads are high-frequency compared to
   step events. Every count is derived from `ReadingEvent` rows, so that
   table needs a retention policy (e.g. 400 days) or it becomes the biggest
   table in the system.
