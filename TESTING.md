# Testing

How SharedLLM is tested, and what CI runs.

## What CI runs (`.github/workflows/`)

| Workflow | Runs |
|---|---|
| `soa_tests.yml` | `ruff` syntax/undefined-name check on `services/gateway` and `pytest --collect-only` as a fast import gate, then the whole Python suite except `local_only`/`server_only` markers (roots from `pytest.ini`: `tests`, `services`, `test`) |
| `ui-tests.yml` | UI lint (non-blocking), `npm run typecheck`, unit tests (`npm run test`, vitest), `npm run build` |
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

Some suites skip when an optional dependency is missing locally (for example
`sqlite_vec` for RAG, `resemblyzer` for voice signatures); those run fully in
the service images. `scripts/run_ci_unit_tests.sh` is a quick subset run by
the Workspace Runtime's `/api/admin/tests/unit` endpoint.

## Live and integration tests

- `test/local/`, `test/integration/`, `tests/integration/`: need running
  services (real Home Assistant, Nextcloud, Raven). Marked `local_only` or
  gated on env such as `LIVE_E2E=1`; they read `INTERNAL_SECRET` and API keys
  from the environment only.

### The live opt-in

A bare `pytest` never talks to a deployed stack. Every test under
`tests/integration/`, `test/integration/`, `test/local/` and `scripts/`, and
every test marked `live` anywhere (for example
`services/execution/tests/test_live_integration.py`,
`services/tests/test_raven_user_cases.py`, `test_gateway_coding.py`), is
skipped with the reason "targets a deployed stack" unless the run opts in:

```bash
python3 -m pytest tests/integration/test_regression.py --run-live --run-local   # flag
SHAREDLLM_RUN_LIVE=1 python3 -m pytest tests/integration --run-local            # or env
```

Only the shell can opt in. The root `conftest.py` reads `SHAREDLLM_RUN_LIVE`
before it loads `.env`/`.env.test` and drops any value those files set, so a
`.env` holding `SERVER_IP`, `INTERNAL_SECRET` or `GH_TOKEN` (or even
`SHAREDLLM_RUN_LIVE=1`) cannot turn an ordinary run into Raven missions and
other writes against production. The opt-in sits on top of the existing gates:
`local_only` still needs `--run-local`, `server_only` `--run-server`, and the
Raven runs `LIVE_E2E=1` and `GH_TOKEN`. Modules that probe a gateway at import
time check the opt-in before probing, so without it they make no request.

CI is unchanged: `soa_tests.yml` and `e2e-tests.yml` set `SHAREDLLM_RUN_LIVE=1`.
That is safe there because a runner has no `.env` and no route to a deployment;
the e2e jobs aim these tests at the compose stack they start themselves.

### Shared config modules

`services/config.py` and `services/gateway/config.py` copy the environment
into module attributes once, at import. The autouse `_isolate_config_modules`
fixture in `conftest.py` puts back the module objects, the package attributes
and their namespaces after every test, so a test that re-imports or reloads
one cannot leave later tests patching a module the code under test no longer
reads. A test that needs a fresh copy should load it privately (see
`services/tests/test_mass_config.py`) rather than reload the shared one.
- `soa_smoke_test.py` (repo root): end-to-end health and routing on a live
  deployment. Run directly, or from the **JarvisLab** dashboard (`/lab`), which
  calls Workspace Runtime's `/api/admin/tests/smoke`.

## Rules

- New services use the FastAPI `lifespan` pattern.
- Test and admin endpoints are protected (`X-Internal-Secret` or admin auth).
- Never hard-code secrets in tests or scripts; read them from the environment.
