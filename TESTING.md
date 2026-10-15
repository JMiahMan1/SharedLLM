# Testing

How SharedLLM is tested, and what CI runs.

## What CI runs (`.github/workflows/`)

| Workflow | Runs |
|---|---|
| `soa_tests.yml` | `pytest --collect-only` as a fast import gate, then the whole Python suite (`pytest`, roots from `pytest.ini`: `tests`, `services`, `test`) |
| `ui-tests.yml` | UI lint (non-blocking), `npm run typecheck`, unit tests (vitest), `npm run build` |
| `e2e-tests.yml` | `tests/unit`, service contracts, `tests/integration` and the `tests/` end-to-end suite against a composed stack |
| `build-images.yml` | builds and pushes every service image on push to `microservices` |
| `android-build.yml` | builds the APK (published by `scripts/deploy_remote.sh`) |
| `DOCUMENTATION.yml` | markdownlint + `doc_validator.sh` |

## Running tests locally

```bash
python3 -m pytest -q -p no:cacheprovider services/<service>/tests   # one service
python3 -m pytest -q                                                # everything
cd services/ui && npx vitest run && npm run -s typecheck && npx eslint src
```

Some suites skip when an optional dependency is missing locally (e.g.
`sqlite_vec` for RAG, `resemblyzer` for voice signatures); those run fully in
the service images. `scripts/run_ci_unit_tests.sh` is a quick subset used by
the Workspace Runtime's "unit tests" admin endpoint.

## Live and integration tests

- `test/local/`, `test/integration/`, `tests/integration/`: need running
  services (real Home Assistant, Nextcloud, Raven). Marked `local_only` or
  gated on env such as `LIVE_E2E=1`; they read `INTERNAL_SECRET` and API keys
  from the environment only.
- `soa_smoke_test.py` (repo root): end-to-end health and routing on a live
  deployment. Run directly, or from the **JarvisLab** dashboard (`/lab`), which
  calls Workspace Runtime's `/api/admin/tests/smoke`.

## Rules

- New services use the FastAPI `lifespan` pattern.
- Test and admin endpoints are protected (`X-Internal-Secret` or admin auth).
- Never hard-code secrets in tests or scripts; read them from the environment.
