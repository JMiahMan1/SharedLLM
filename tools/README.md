# SharedLLM Tools

Command-line tools that the execution service runs as subprocesses. They are
copied into the execution image, so a change here rebuilds that image
(`.github/workflows/build-images.yml`).

| Tool | Used by | Purpose |
|---|---|---|
| `code_search.py` | `services/execution/handlers/code_search.py` | Search GitHub and GitLab repositories for code (`--query`, `--sources`, `--language`, `--owner`, `--repo`) |
| `webscraper.py` | `services/execution/handlers/webscraper.py` | Capture product pages with Camoufox or Playwright and extract prices and text from the screenshots with `vision_ocr.py` |
| `vision_ocr.py` | `webscraper.py` | Extract text from screenshots with the Qwen2.5-VL vision model through the local LLM proxy (reads `INTERNAL_SECRET` from the environment) |

Each tool can also be run by hand, for example:

```bash
python tools/code_search.py --query "get_password_hash" --sources github gitlab
```

## Related

- `scripts/`: deploy, CI, benchmark and live diagnostic scripts (`docs/SCRIPTS.md`)
- `tests/`, `test/`, `services/*/tests/`: pytest suites (`TESTING.md`)
