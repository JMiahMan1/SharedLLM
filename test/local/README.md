# Local hardware / live tests

Everything here is marked `local_only` and talks to real services. Household
values (users, entities, hosts) are never written into the files: pass them as
environment variables (or put them in your untracked `.env`). A test skips with
a reason when a flag it needs is missing.

| Flag | Used by | Meaning |
|---|---|---|
| `LIVE_TEST_URL` | most | Base URL of the service under test. |
| `INTERNAL_SECRET` | most | The shared internal secret. |
| `HA_URL`, `HA_TOKEN` | `test_auth_identity`, `test_hardware_state`, `test_office_tv_media_controls` | Home Assistant to verify against. |
| `HA_DEFAULT_USER` | `test_auth_identity`, `test_rag_sync` | The household's RAG/HA user. No default. |
| `NEXTCLOUD_USER` | `test_auth_identity` | Expected decrypted Nextcloud user. |
| `LIVE_HA_ENTITY_QUERY` | `test_rag_sync` | A friendly name that exists in your Home Assistant (e.g. "piano lamp"). |
| `LIVE_TV_ENTITY`, `LIVE_TV_NAME` | `test_office_tv_media_controls` | The TV's Music Assistant entity id and the name you say to Jarvis. |
| `NC_URL`, `NC_USER`, `NC_PASS` | `test_storage_live` | Nextcloud account for the storage tests. |
| `VISION_OCR_URL`, `VISION_OCR_MODEL` | `test_recipe_ocr_live` | Vision model endpoint. |

Run with `--run-local`, e.g.

    HA_DEFAULT_USER=<user> LIVE_HA_ENTITY_QUERY="<entity name>" \
        .venv/bin/python -m pytest test/local/test_rag_sync.py -m local_only --run-local -s
