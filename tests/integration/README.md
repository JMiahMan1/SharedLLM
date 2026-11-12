# Live integration tests

Most files here drive a deployed stack and are marked `local_only` (CI runs
`-m "not local_only and not server_only"`). Real hosts, accounts and devices are
never written into these files: they come from the environment at run time, and
a test skips with a reason when what it needs is not set.

| Flag | Used by | Meaning |
|---|---|---|
| `SERVER_IP` | `test_live_functional`, `test_llm_settings`, `test_regression`, `test_raven_*` | Host running the deployed stack. Required; there is no default. |
| `GATEWAY_URL`, `IDENTITY_URL`, `STORAGE_URL`, `LOGGING_URL`, `WORKSPACE_RUNTIME_URL` | same | Override a single service URL (default: `http://$SERVER_IP:<port>`). |
| `INTERNAL_SECRET` | most | The shared internal secret. |
| `LIVE_E2E=1`, `GH_TOKEN` | `test_raven_*` | Opt in to the Raven end-to-end runs (they create real GitHub repos). |
| `GH_OWNER` (and optional `GH_USER`) | `test_raven_3d_space_shooter`, `test_raven_python_basics`, `test_raven_space_shooter_python`, `test_raven_user_cases`, `test_raven_autonomous_harness` | GitHub account Raven pushes to. Required; there is no default. |
| `TEST_REPO_URL`, `TEST_REPO_BRANCH` | `test_workspace_lifecycle` | Repo to clone (default: a public sample repo). |

Example:

    LIVE_E2E=1 GH_TOKEN=... GH_OWNER=<github-account> SERVER_IP=<server> \
        pytest tests/integration/test_raven_user_cases.py --run-local -s
