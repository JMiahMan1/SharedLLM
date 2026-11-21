# Workspace Studio: code, tests, previews, art and decks in one workspace

> The Workspace is not only a folder of files. It is where a project runs, where
> its tests prove it, where a dev server is previewed on a phone, and where the
> art, narration, slides and games for it are made. Code:
> `services/gateway/studio/`. Surface: Workspace → **Tools → Studio**, and
> `/api/studio/*` for anything that talks to us.

## 1. Why it exists

Raven could write code into a workspace, but nothing in the product knew *what
kind of project* it was looking at. "Run the tests" meant guessing between
`pytest`, `npm test`, `go test`, `cargo test`, `mvn test` and `dotnet test`, and a
wrong guess wasted a sandbox run. The studio answers three questions and then
stays out of the way:

1. **What is this?** — detect the stack from the workspace's own files.
2. **How do you run it?** — the commands come from that detection, never from a
   model's guess, so the same buttons work for any project.
3. **What did it do?** — the exit code, the tail of the output, and (for a
   preview) the URL with a check that something actually answered.

## 2. Detection (`studio/profiles.py`)

Pure, table-driven, tested without a container. One profile per stack:

| Detected from | Stack | test | build | preview |
|---|---|---|---|---|
| `package.json` + `vite.config.*` | node-vite | `npm test` | `npm run build` | `npm run dev -- --host 0.0.0.0` on **5173** |
| `package.json` + `next.config.*` | node-next | `npm test` | `npm run build` | `npm run dev` on **3000** |
| `package.json` | node | `npm test` | `npm run build` | `npm start` on 3000 |
| `pyproject.toml` / `requirements.txt` | python | `python -m pytest -q` | `python -m compileall -q .` | — |
| `go.mod` | go | `go test ./...` | `go build ./...` | `go run .` on 8080 |
| `Cargo.toml` | rust | `cargo test` | `cargo build` | `cargo run` |
| `pom.xml` / `build.gradle` | java | `mvn -q test` / `./gradlew test` | package / build | — |
| `*.sln`, `*.csproj` | dotnet | `dotnet test` | `dotnet build -c Release` | — |
| `index.html` | static | — | — | `python3 -m http.server 8080` |
| nothing | unknown | — | — | — |

Node is checked before Python on purpose: a Vite app often carries a stray
`requirements.txt` for a helper script and never the other way round. The package
manager is read from the lockfile (`pnpm-lock.yaml`, `yarn.lock`, else npm).
`all_stacks` reports every stack present, because a repo can hold a Python
service and a Vite app at once.

`unknown` is a real answer, not a failure: a folder of Markdown and pictures can
still hold a deck or a game.

## 3. Running it (`studio/runtime.py`)

Everything executes in the workspace's own **sandbox container**
(`services/workspace_sandbox`): only that directory is mounted, it runs as a
non-root user on a private network with CPU/memory/PID limits, and dangerous
system commands are refused by the shell handler. The studio decides *what* to
run; the sandbox decides *whether it may*.

```
POST /api/studio/run  {"workspace_id": "sermons", "action": "test"}
→ {"action": "test", "command": "python -m pytest -q", "ok": true, "returncode": 0,
   "duration_s": 12.4, "stdout": "128 passed", "stderr": ""}
```

A failure is reported as a failure: the exit code and the **tail** of the output
(6 000 characters, with a note saying how much was cut). A step the stack does not
have is refused with the list it does have.

## 4. Previewing it

A dev server is started **detached** inside the sandbox (`nohup … &`, PID written
to `.studio/preview.pid`, log to `.studio/preview.log`), its port is published
through the Workspace API, and the URL is polled until something answers:

```
POST /api/studio/preview   {"workspace_id": "arcade"}
→ {"ok": true, "url": "http://llm-host:14173", "container_port": 5173, "answered": true}
POST /api/studio/preview/stop {"workspace_id": "arcade"}
→ {"stopped_process": true, "closed_ports": [14173]}
```

Stopping kills the recorded PID and closes **every** port the workspace published,
so a preview never leaks a listener. If the process already died, the ports are
still closed and the answer says the process was gone (`"process": "gone"`),
that is not an error. A kill that fails, a port list that cannot be read, or a
forward that will not close **is** an error naming it (after every other port has
been tried). Starting a preview stops any earlier one first, and its answer says
what it replaced (`"replaced"`). A server that takes its port as an argument
(the static site's `http.server {port}`) is started on the requested `port`.
A server that starts but never answers says so, with its own log, instead of
leaving you with a dead link.

## 5. Making things: art, narration, decks, games

| Endpoint | What it writes |
|---|---|
| `POST /api/studio/assets/image` | `assets/<name>.png` from a prompt (Alpaca's image proxy) |
| `POST /api/studio/assets/narration` | `assets/<name>.wav` from text (the TTS engine) |
| `POST /api/studio/deck` | `decks/<slug>/index.html` + `source.md` |
| `POST /api/studio/game` | `games/<slug>/index.html` + `README.md` |
| `GET /api/studio/assets` | the manifest: what was made, from which prompt and model |

Every generated file is recorded in `assets/manifest.json` with the prompt, the
model and the time, because art you cannot re-make is art you cannot fix.
Re-generating the same name replaces the entry rather than piling up duplicates.
The manifest is read whole and written back only if it is still what was read
(`expected_sha256`; a concurrent writer means read and merge again). A manifest
that is corrupt or cannot be read is an error, never treated as empty, so it is
never overwritten with one entry.

A deck or game that already exists is **not** overwritten: the answer is a `409`
naming the file, and `"replace": true` rebuilds it (hand edits to it are lost).
The panel asks before sending that. Titles are HTML-escaped and sprite paths are
JSON-escaped for a `<script>` element, so neither can inject markup.

**Decks** are Markdown in, one self-contained HTML file out:

```markdown
# The God Who Works
- Romans 8:28
- Three points
:: slow down on point two

---

# He works in the waiting
![field](assets/field.png "A field at dawn")
- Not late, not early
```

`---` splits slides, `#` is the title, `-` a bullet, `>` a quote, `![alt](path)`
a picture, `::` speaker notes. The output has keyboard and swipe navigation,
works offline (no CDN, no fonts, no scripts fetched) and prints one slide per
page. The Markdown is kept next to it, so the deck can be re-built after edits.

**Games** are a playable single-file canvas template (keyboard, touch, score,
lives, optional sprites) plus a README that says which line to change and how to
point it at generated art. It is a starting point Raven or a child can edit, not a
framework.

## 6. The shell inside the sandbox (and why it was broken)

Every studio action ends up in `POST /workspace/shell`, which runs the command
inside the workspace's sandbox container as uid 1000. Three things about that
environment are easy to get wrong, and all three were:

1. **`HOME` is the sandbox user's, not ours.** The shell used to be given
   `HOME=/home/sharedllm` — a directory that exists only in the gateway image.
   Inside the sandbox (user `raven`, home `/home/raven`) npm's cache, `pip
   --user`, `git config --global`, cargo's registry and bash's own startup all
   failed with `EACCES`, which reads exactly like "the terminal is broken".
   `workspace_sandbox.py` now reads the home from the container's own passwd
   entry (one cached `getent passwd` exec per container), falls back to `/tmp`
   when there is no answer, and always sets `HOME` **last**, so a caller cannot
   point it somewhere unwritable.
2. **The service environment is not the sandbox's.** The shell previously sent
   `os.environ` — `INTERNAL_SECRET`, `FERNET_KEY`, cloud API keys — into
   arbitrary repository code. Now only an allowlist crosses: `PATH`, `LANG`,
   `TERM`, `TZ`, plus the caller's own credentials and the workspace's own `env`.
3. **A person's tools must be able to write somewhere.** With a correct `HOME`,
   `npm test` and `pip install --user` work without root, which is the whole
   reason the sandbox runs as a normal user.

`$HOME` is never something a caller should send. The sandbox derives it; a test
(`test/unit/test_workspace_sandbox.py`) pins that a caller's `HOME` loses, that
the passwd value wins, that `/tmp` is the fallback, and that no service secret
reaches the sandbox.

## 7. Is it working? Ask it

"It is broken" is not a diagnosis, so the studio answers two questions itself:

**`POST /api/studio/diagnose`** runs six safe probes and reports each one with its
own output, never stopping at the first failure:

| probe | what it proves |
| --- | --- |
| `identity` | which user the sandbox actually runs as |
| `home` | `$HOME` and whether it is **writable** (exits non-zero when it is not) |
| `directory` | where the shell starts and whether it can list the project |
| `bash` | `/bin/bash -lc` itself — the thing the Terminal opens |
| `python` | `python3 --version` |
| `git` | `git --version` |

The response carries `failed` (the names) and a `hint` that names the cause:
an unwritable HOME, a missing bash, a container that is not running, a workspace
the sandbox cannot read, a missing toolchain.

**`POST /api/studio/verify`** proves it end to end: diagnose the shell, write a
one-line program (`_studio_probe.py` or `.mjs`), run it, delete it, then run the
detected project's test step — and return one verdict:

```json
{"ok": false, "stack": "python",
 "shell": {"ok": true, "failed": []},
 "program": {"ok": true, "output": "studio-ok from python"},
 "tests": {"ok": false, "stderr": "1 failed, 4 passed"},
 "hint": "the tests fail (`python -m pytest -q`): 1 failed, 4 passed"}
```

The order matters: a broken shell is reported as a shell problem and the check
stops there (`program` and `tests` are `null`): nothing else is run or blamed. The
probe file is removed even when it fails, a probe is not a deliverable.

`ok` needs proof: a one-line program that ran, or tests that ran and passed. A
stack with no probe (`"skipped": true`) and no test step is "nothing proved code
runs here", not a pass. A test step that exits 0 having run **no tests** (`go test`
on a package without tests, `cargo test` with `running 0 tests`, pytest's
`no tests ran`) is a failure, and every test run reports `extra.tests_ran` (the
count read from the runner's summary; `null` when the runner does not say). A
shell answer counts as success only with status `SUCCESS` **and** exit code 0.

## 8. 3D models: reading and editing STL

`studio/stl.py` opens both STL flavours in pure Python (no numpy, no trimesh) and
edits them:

- **read** — a binary header + 50 bytes per facet, or ASCII `facet normal …`;
  a truncated file is named as truncated, an empty one as empty, a mesh over
  400 000 triangles as too large to edit here;
- **measure** — bounding box, size, centre, surface area, signed volume by the
  divergence theorem, distinct vertices, and `open_edges` (edges used by one
  triangle only, i.e. a surface with holes);
- **warn** — open surface, inverted winding (negative volume = inside out),
  zero-area facets, a unit guess (STL carries none: millimetres unless the model
  is metres across);
- **edit** — `rotate` (degrees, X then Y then Z), `fit` (longest side), `scale`
  (uniform or per-axis), `center`, `translate`, in that order, which is the order
  a person uses them.

An edit writes a **new** file (`bracket.stl` → `bracket_edited.stl`, then
`bracket_edited_2.stl` ...) and returns the measurements before *and* after, so
the caller quotes both instead of trusting the resize. A named `out_path` that
exists is refused (`409`) unless `replace` is set, and the source itself can never
be the output. Paths are workspace-relative `.stl` files: an absolute path is a
`400` here and a `403` in the Workspace Runtime, which confines every absolute
path to the caller's **own** workspace (it used to accept anything under the
workspace root, i.e. other users' files). A non-finite number, a vector that is
not three numbers, or a scale list of the wrong length is a `400`. A negative
scale is refused (it would mirror the solid inside out); rotation never changes
the volume, which is what the tests assert.

Parsing and editing run in a worker thread (at most two at once), never on the
gateway's event loop; a file over 64 MiB is refused before it is buffered, and
the ASCII parser stops at the triangle cap.

`GET /api/studio/stl/preview` returns an indexed mesh (shared vertices merged,
capped at `limit` triangles) for a browser viewer.

## 9. What the studio refuses to do

- **No free-form commands.** `action` names a profile step; the command comes from
  the profile. A caller cannot pass a shell line through `/api/studio/run`.
- **No silent success.** A non-zero exit is a failure in the response, and a
  preview that does not answer is not reported as live.
- **No placeholder art.** A backend that returns nothing is a `502` with its own
  words ("CUDA out of memory"), not a blank image.
- **No hidden listeners.** Stopping a preview always closes the published ports.
- **No fabricated geometry.** A model that does not parse is an error naming why;
  an edit that would mirror a solid inside out is refused.
- **No leftover probe.** `verify` deletes what it ran, even on failure.

## 10. The model's tools

The workspace chat and Raven reach the studio as tools, not by HTTP: the sandbox
cannot call the gateway, and the tool registry had no row for the routes the RAG
lessons taught. `tool_registry` now has one row per action (`StudioProfileRequest`,
`StudioDiagnoseRequest`, `StudioVerifyRequest`, `StudioRunRequest`,
`StudioPreviewRequest`, `StudioPreviewStopRequest`, `StudioAssetsRequest`,
`StudioImageRequest`, `StudioNarrationRequest`, `StudioDeckRequest`,
`StudioGameRequest`, `StlInspectRequest`, `StlTransformRequest`), served by the
gateway itself. The single-turn executor and Raven's agent loop run them
in-process through `studio.tools.call`, which validates the payload with the
route's own model and runs the route's own handler **as the turn's user**, so the
Workspace Runtime applies that user's access exactly as for the button. The
external OpenAI-compatible agent reaches the same routes with the caller's own key.
A tool result is the whole answer with `ok` first, so a failing test run reaches
the model as a failure.

## 11. Where it lives

- `services/gateway/studio/profiles.py` — detection and commands (pure)
- `services/gateway/studio/runtime.py` — run, preview, stop, diagnose, verify
- `services/gateway/studio/stl.py` — reading, measuring and editing STL models (pure)
- `services/gateway/studio/media.py` — image generation and narration into `assets/`
- `services/gateway/studio/deck.py`, `templates.py` — the deck and game documents
- `services/gateway/studio/works.py` — writing decks and games (with their READMEs)
- `services/gateway/studio/actions.py`: every action once: request model, handler, error to status
- `services/gateway/studio/routes.py`: `/api/studio/*` (thin: authenticate, run the action)
- `services/gateway/studio/tools.py`: the same actions as model tools
- `services/gateway/workspace_api.py` — the one client for files, raw bytes, ports and tests
- `services/workspace_sandbox.py` — the sandbox environment (`build_exec_env`, `_home_for_uid`)
- `services/ui/src/components/workspace/WorkspaceStudio.tsx` — the panel in
  Workspace → Tools
- `docs/api_reference.md` — the endpoints, with the rest
