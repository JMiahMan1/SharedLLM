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
still closed and the answer says the process was gone — that is not an error.
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

## 6. What the studio refuses to do

- **No free-form commands.** `action` names a profile step; the command comes from
  the profile. A caller cannot pass a shell line through `/api/studio/run`.
- **No silent success.** A non-zero exit is a failure in the response, and a
  preview that does not answer is not reported as live.
- **No placeholder art.** A backend that returns nothing is a `502` with its own
  words ("CUDA out of memory"), not a blank image.
- **No hidden listeners.** Stopping a preview always closes the published ports.

## 7. Where it lives

- `services/gateway/studio/profiles.py` — detection and commands (pure)
- `services/gateway/studio/runtime.py` — run, preview, stop
- `services/gateway/studio/media.py` — image generation and narration into `assets/`
- `services/gateway/studio/deck.py`, `templates.py` — the deck and game documents
- `services/gateway/studio/works.py` — writing decks and games (with their READMEs)
- `services/gateway/studio/routes.py` — `/api/studio/*`
- `services/gateway/workspace_api.py` — the one client for files, ports and tests
- `services/ui/src/components/workspace/WorkspaceStudio.tsx` — the panel in
  Workspace → Tools
- `docs/api_reference.md` — the endpoints, with the rest
