# Connection page overhaul — design spec

**Date**: 2026-05-20
**Status**: Approved, awaiting implementation plan

---

## Goal

Make the web UI's Connection page understandable for a user who has just installed mongosemantic and knows nothing about it. The page must answer three questions clearly:

1. **What am I connected to right now?**
2. **Is that connection still alive?**
3. **How do I connect to something different (or first-time connect)?**

…with explicit error handling and dev help.

## Non-goals

- Hot-swapping connections mid-session. We accept "restart required" as the trade-off.
- Multi-connection management (saved profiles, named environments). Single active connection only.
- Persisting embedding model selection here. Model is shown as status; it remains configured via `MONGOSEMANTIC_MODEL` env var (own page later).
- Cross-process connection-change notification to the worker. Worker reads config at its own startup.

---

## User mental model

The connection page is the source of truth for "what database the app is connected to," in the same way a phone's wifi settings page is the source of truth for which network it's on. The user can: see the current connection, ping it to confirm it's alive, swap it for a new one, or disconnect entirely. Every change requires a restart of `mongosemantic ui` (and `mongosemantic worker` if running) — this is communicated explicitly.

---

## States

The page renders one of three top-level states, determined server-side at request time:

### State 1 — Not connected
- **When**: no saved config file AND no `MONGOSEMANTIC_URI` env var.
- **UI**: empty form (URI + database inputs), friendly hero "Not connected yet — paste a MongoDB URI below to get started", single **Connect** button. Dev-help panel always visible below.
- **Side effect**: other left-nav pages (Collections, Search, Visualize, Dashboard) are disabled with tooltip "Connect to a database first."

### State 2 — Connected (UI-owned)
- **When**: config file has a saved URI, no `MONGOSEMANTIC_URI` env var override.
- **UI**: status card (URI redacted, database, topology, MongoDB version, embedding model, configured-collections count) with three buttons:
  - **Test connection** — pings the active connection, shows pass/fail + latency.
  - **Change connection** — reveals the form, prefilled with current values. Saving overwrites the config and shows a "restart required" banner.
  - **Disconnect** — confirm modal → clears saved config → "restart required" banner.
- All other nav pages enabled.

### State 3 — Connected (env override)
- **When**: `MONGOSEMANTIC_URI` env var is set at startup.
- **UI**: same status card as State 2, plus a **blue info banner** at top: "Running from `MONGOSEMANTIC_URI` env var. To make changes, edit the env var and restart, or unset it and use this page." Form, Change, and Disconnect buttons are hidden. Only **Test connection** is available. Read-only mode.

---

## Persistence

**Config file**: `$XDG_CONFIG_HOME/mongosemantic/config.json` (falls back to `~/.config/mongosemantic/config.json`). Created with directory mode `0700` and file mode `0600`.

**Schema**:
```json
{
  "uri": "mongodb+srv://user:pass@cluster.mongodb.net/",
  "database": "sample_mflix",
  "saved_at": "2026-05-20T15:30:00Z"
}
```

**Startup precedence** (in `Settings.from_environment()`):

1. If `MONGOSEMANTIC_URI` env var is set → use env vars (current behavior). State 3.
2. Else if config file exists and parses → use config file. State 2.
3. Else → no connection. State 1.

The same precedence applies to `mongosemantic ui` and `mongosemantic worker`. Both processes read the config file at startup; neither watches it for changes.

---

## Connect flow (save + restart-required)

1. User submits the form. Client POSTs `/api/connection/save` with `{uri, database}`.
2. Server validates URI prefix (`mongodb://` or `mongodb+srv://`) and that database is non-empty.
3. Server opens a **temporary** `MongoConnection` to test the URI (5-second `serverSelectionTimeoutMS`). Gathers topology + Mongo version from the temporary connection.
4. **On failure**: return `{ok: false, error: {code, message, hint, details}}` — config is NOT written. The running session is unaffected.
5. **On success**: write the config file, close the temporary connection, return `{ok: true, topology, mongo_version, restart_required: true}`.
6. Client renders a green banner: **"Saved. Restart `mongosemantic ui` (and `mongosemantic worker` if you have one running) to start using this connection."** Status card re-renders to show the saved (but not-yet-active) URI with a "pending restart" badge.

The running server keeps using the old connection until restart. All other pages continue to function against the old URI. There is no in-process hot-swap.

**Change connection** uses the identical endpoint (overwrites the file). **Disconnect** uses `DELETE /api/connection` to remove the file; same restart-required banner follows.

---

## Error mapping

Implemented in `mongosemantic/web/connection_errors.py` as a pure function `map_exception(exc) -> ConnectionError`. The function takes a PyMongo exception and returns `{code, message, hint, details}`. Tested with constructed exception fixtures.

| Trigger | code | User-facing message | Hint |
|---|---|---|---|
| URI doesn't start with `mongodb://` or `mongodb+srv://` | `bad_scheme` | "URI must start with `mongodb://` or `mongodb+srv://`." | "Copy it from Atlas → Connect → Drivers." |
| `ConfigurationError` "Empty host" / malformed URI | `malformed_uri` | "Couldn't parse the URI. Check for missing characters around `@` or the host." | "Format: `mongodb+srv://user:pass@cluster.mongodb.net/`" |
| `OperationFailure` code 18 (Auth failed) | `auth_failed` | "Username or password rejected." | "Atlas: check Database Access. URL-encode special characters in the password (e.g. `@` → `%40`)." |
| DNS / `gaierror` / SRV lookup failure | `dns_failure` | "Can't resolve the cluster hostname." | "Check the URI for typos, or your network/DNS." |
| `ServerSelectionTimeoutError` with "IP that isn't whitelisted" in detail | `ip_not_allowlisted` | "Atlas refused the connection — your current IP isn't allowlisted." | "Add it under Atlas → Network Access, then try again." |
| `ServerSelectionTimeoutError` generic | `timeout` | "Couldn't reach the cluster within 5 seconds." | "Common causes: cluster paused, IP not in Atlas Network Access, firewall blocking port 27017." |
| TLS / `SSL` errors in message | `tls_failure` | "TLS handshake failed." | "mongosemantic uses the certifi CA bundle by default. If you're behind a corporate proxy, set `SSL_CERT_FILE` to your proxy's CA." |
| Connected but `database` not readable | `db_not_readable` | "Connected to the cluster, but database `<X>` is not readable with these credentials." | "Check the database user's roles (needs `read` on this database)." |
| Any other exception | `unknown` | exception class name + short message | (raw message) |

All error responses include a `details` field with the original exception's `repr()` for the "Show technical details" disclosure. The mapping is the authoritative copy; `content.py` references it.

---

## Dev help panel

Collapsible panel below the form/status card, visible in all three states.

- **Current env state** — show whether `MONGOSEMANTIC_URI`, `MONGOSEMANTIC_DB`, `MONGOSEMANTIC_MODEL` are set (yes/no only; never echo URI value).
- **Config file path** — show `~/.config/mongosemantic/config.json` (or the XDG override) as plain text.
- **Quick reference**:
  - "URI format: `mongodb+srv://user:pass@cluster.mongodb.net/`"
  - "Atlas users: Network Access → add your IP; Database Access → create user with `readWriteAnyDatabase`."
  - "After saving, restart `mongosemantic ui` (and `mongosemantic worker` if running)."
- **Last test result** — if Test connection has been run this session: pass/fail + ping latency in ms + timestamp. Otherwise omitted.

---

## API surface

All under `mongosemantic/web/routes/system.py`. Existing `POST /api/connect` is removed.

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/api/connection` | — | `{state, uri_redacted, database, topology, mongo_version, model, configured_count, env_overrides: {uri, db, model}}` |
| `POST` | `/api/connection/save` | `{uri, database}` | `{ok: true, topology, mongo_version, restart_required: true}` or `{ok: false, error: {code, message, hint, details}}` |
| `POST` | `/api/connection/test` | — | `{ok: true, latency_ms, mongo_version}` or `{ok: false, error: {...}}` |
| `DELETE` | `/api/connection` | — | `{ok: true, restart_required: true}` |

`state` is one of `"not_connected"`, `"connected_ui"`, `"connected_env"`.
`uri_redacted` masks credentials: `mongodb+srv://<redacted>@cluster…`.
`GET /api/topology` is retained unchanged for backward compat, but `app.js` switches to `/api/connection` for richer info.

---

## File layout

**New backend modules**:
- `mongosemantic/connection_store.py` — pure `load() / save(uri, database) / delete() / config_path()`. Handles XDG, dir creation, chmod. Fully unit-testable. (Note: distinct from existing `mongosemantic/state/config_store.py`, which is per-collection semantic-search config in MongoDB.)
- `mongosemantic/web/connection_errors.py` — `map_exception(exc) -> ConnectionError` + Pydantic models for the JSON shape.

**Changed backend modules**:
- `mongosemantic/config.py` — add `Settings.from_environment()` classmethod that layers env → config_store → defaults. Existing `Settings()` continues to work (defers to the new classmethod).
- `mongosemantic/web/routes/system.py` — replace endpoints per the table above. Inject `config_store` for testability.
- `mongosemantic/web/content.py` — add `connection.*` keys for all new copy.

**Frontend**:
- `mongosemantic/web/static/index.html` — replace `#page-connection`'s contents with the three-state structure (only structural skeleton; copy lives in `content.py`).
- `mongosemantic/web/static/app.js` — connection page state machine (`not_connected | connected_ui | connected_env | connecting | saved_pending_restart`). Disable-nav-when-not-connected helper.
- `mongosemantic/web/static/style.css` — status card, inline error block, info/success banners, dev-help collapsible.

---

## Testing

**Unit tests** (`tests/unit/`):

- `test_connection_store.py`:
  - Write a config; read it back; assert `0600` permissions.
  - `$XDG_CONFIG_HOME` override is honored.
  - `delete()` removes the file and is idempotent.
  - Loading a missing file returns `None` (not an exception).
  - Loading a malformed file returns `None` and does not crash startup.
- `test_connection_errors.py`:
  - One test per row in the error mapping table. Build a fake exception that matches the trigger, assert the returned code, message, and hint.
- `test_settings_precedence.py`:
  - env var > config file > defaults. Three sub-cases.
  - State derivation: `(env_uri_set, config_file_exists) → state` truth table.
- `test_routes_system_connection.py`:
  - `GET /api/connection` in each state.
  - `POST /api/connection/save` happy path writes config, returns `restart_required`.
  - `POST /api/connection/save` failure does NOT write config.
  - `DELETE /api/connection` removes config.
  - All endpoints use a fake `config_store` (no real filesystem) and a fake `MongoConnection` (no real DB).

**Integration test** (`tests/integration/atlas/`):

- `test_t8_connection_page.py`:
  - POST `/api/connection/save` against the real Atlas URI from `.atlas.env`.
  - Read back via `GET /api/connection`; assert URI is saved, state is `connected_ui`.
  - Reload `Settings.from_environment()` to simulate restart; assert it now reads from the config file.
  - Clean up by `DELETE /api/connection`.

**Manual UI smoke** — added to Tier 7 of `docs/superpowers/specs/2026-05-19-atlas-verification-design.md`:
- First-run "Not connected" state with empty `~/.config/mongosemantic/`.
- Connect with a known-good URI; expect green banner; verify config file written `0600`.
- Disconnect; verify file removed.
- Run with `MONGOSEMANTIC_URI` set; expect env-override banner; Change/Disconnect hidden.
- Trigger each error type (bad scheme, bad password, paused cluster, wrong IP) and verify the friendly message + hint render.

---

## Rollout / compatibility

- Existing users who launch with `MONGOSEMANTIC_URI` set keep working unchanged. They land in State 3 ("env override") with the info banner.
- No existing `MONGOSEMANTIC_*` env var is dropped.
- `POST /api/connect` is removed; no external consumers (UI-only endpoint, replaced by `/api/connection/save`).
- Config file is new; absence is benign.
- Version bump: patch (`0.7.6` → `0.7.7`) — non-breaking addition.

---

## Open items deferred to plan / implementation

- Exact copy strings (placeholders OK in this spec; finalized in `content.py` during implementation).
- Confirm modal markup (existing modal patterns in `app.js` will be reused).
- "Reveal config file" — out of scope for v1 (platform-specific).
- Per-collection model display in the status card — depends on whether `Settings.configured_count` is cheap; if it requires a slow query, omit and just show "N collections configured" lazily on hover.
