# Contributing

Practical guide for working on mongosemantic — setup, tests, code style,
and where to add common kinds of features.

For project architecture see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
For the current state of the project see [`docs/HANDOFF.md`](docs/HANDOFF.md).

---

## Local setup

```bash
git clone https://github.com/varmabudharaju/mongosemantic
cd mongosemantic
pip install -e ".[dev,openai]"        # editable install + dev + openai extras
docker compose up -d                  # replica set on :27117, standalone on :27219
```

That's it. No node_modules, no separate build step. Run the test suite
to confirm everything's wired:

```bash
python3 -m pytest tests/unit -q       # 191 tests, ~10s, offline
```

For the integration tests you need the docker containers up:

```bash
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration -q
# 10 tests, ~20s
```

---

## Code style

Enforced by `ruff` (config in `pyproject.toml`):

```bash
ruff check .                          # lint
ruff check . --fix                    # auto-fix the easy ones
```

There's no separate formatter — ruff's `I` (isort) and `UP` (pyupgrade)
rules cover most of it. We don't run `black` or any other formatter on
top.

Conventions worth knowing:

- **Type hints everywhere.** New functions get types on their args and
  return value. Mongo result dicts can stay `dict` rather than typed.
- **Docstrings on public behavior, not private helpers.** Module
  docstrings explain *why* the module exists; function docstrings
  explain non-obvious behavior. Don't paraphrase the function name.
- **No trailing commas on single-line collections.** Multi-line
  collections always have them.
- **No `print()` in library code.** Use `console.print()` (rich) in
  CLI command modules; nothing prints from inside `mongosemantic/{db,
  state, search, sync, worker, embeddings}`.
- **Tests use `mongomock` by default**; only escalate to the
  integration suite when the test needs real Mongo (change streams,
  `renameCollection`, etc.).

---

## Where to add things

### A new CLI command

1. Add `mongosemantic/commands/{name}.py` with a single function
   (`{name}_cmd(...)`).
2. Register it in `mongosemantic/cli.py`:
   ```python
   from mongosemantic.commands import {name} as _{name}_mod
   app.command("{name}")(_{name}_mod.{name}_cmd)
   ```
3. Test in `tests/unit/test_cmd_{name}.py` using
   `typer.testing.CliRunner`.

### A new embedding provider

1. Add `mongosemantic/embeddings/{name}.py` implementing the
   `EmbeddingProvider` protocol (`embed(text) -> np.ndarray`,
   `embed_batch(texts) -> np.ndarray`, `dim: int`,
   `model_name: str`).
2. Register the model in `mongosemantic/config.py:MODEL_DIMS` with
   its output dimension.
3. Wire it into `mongosemantic/embeddings/provider.py:get_provider`.
4. Add tests against the provider's expected error shape (network
   failure, dim mismatch). Mock the upstream API.

### A new web API route

1. Add `mongosemantic/web/routes/{resource}.py` with `router = APIRouter()`
   and decorated handlers.
2. Wire it into `mongosemantic/web/app.py`:
   ```python
   from mongosemantic.web.routes import {resource} as _{resource}_routes
   # ...
   app.include_router(_{resource}_routes.router)
   ```
3. POST endpoints require CSRF — the middleware enforces it
   automatically; just make sure your test calls `client.get("/healthz")`
   first to seed the cookie, then sends the matching `X-CSRF-Token`
   header on the POST.
4. Test in `tests/unit/test_route_{resource}.py`. Use the pattern
   from `test_route_collections.py` — TestClient + mongomock +
   `patch("...MongoConnection.open")`.

### A new MCP tool

1. Add the implementation as a plain function in
   `mongosemantic/mcp_server/tools.py` taking a `Database` (or
   `MongoConnection`) plus your kwargs, returning a JSON-serializable
   dict.
2. Register it in `mongosemantic/mcp_server/server.py:create_mcp` with
   a `@app.tool()` decorator. The docstring becomes what Claude sees
   when picking tools — make it concrete and actionable.
3. Bump the tool count in
   `tests/unit/test_mcp_server.py:test_create_mcp_registers_all_*_tools`.
4. Add a tools-level unit test in `tests/unit/test_mcp_tools.py`
   against mongomock.

### A new search mode (e.g. a different scoring function)

1. Add `mongosemantic/search/{mode}.py` with a `build_{mode}_pipeline(...)`
   function. Compare with `atlas.py`, `brute_force.py`, `hybrid.py`,
   `inline.py` for the contract — the pipeline must produce rows with
   `source_id`, `field_path`, `chunk_text`, `score`, `source_doc`.
2. Branch in `mongosemantic/commands/search.py:_run_one_field` (or
   add a sibling helper).
3. Test pipeline shape in `tests/unit/test_search_pipelines.py` — we
   can't execute Atlas-only stages, but we verify the structure.
4. Add a CLI flag and/or MCP tool to expose it.

### A new UI page

1. Add a `<section id="page-{name}" hidden>...</section>` in
   `mongosemantic/web/static/index.html`.
2. Add it to the nav and to the `PAGES` array in
   `mongosemantic/web/static/app.js`.
3. Add a handler function in the `handlers` object of `app.js`.
4. **Include a "How to use" `<aside class="page-help">` block** at
   the top — every other page has one, see existing examples.
5. Add any user-facing copy to `mongosemantic/web/content.py` so
   non-developers can edit it without touching the JS.

---

## Releasing

There's no automated release pipeline yet — every release so far has
been a manual bump + tag + push.

```bash
# 1. Bump version
#    mongosemantic/__init__.py: __version__ = "X.Y.Z"
#    pyproject.toml:           version = "X.Y.Z"

# 2. Write the CHANGELOG entry at the top of CHANGELOG.md.
#    Group by feature area; mention behavior changes the user
#    can observe. Don't bullet implementation details.

# 3. Run the full sweep
python3 -m pytest tests/unit -q
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration -q
ruff check .

# 4. Commit, tag, push
git add -A
git commit -m "docs: vX.Y.Z README + changelog + version bump"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

The `docs/HANDOFF.md` "What's next" section calls out automating this
with `release-please` or `python-semantic-release`. Worth doing if you
make more than a release every few weeks.

---

## What *not* to do

- Don't introduce a build step on the frontend (Webpack, Vite, etc.)
  without a real reason. The "no node_modules" promise is part of why
  this project is small enough for one person to hold in their head.
- Don't add a new code path that embeds documents without going through
  `sync/enqueue.py:enqueue_for_doc`. The chunking, dedup, and stale
  cleanup logic lives there. See the design decision in HANDOFF.md.
- Don't read the embedding model from `MONGOSEMANTIC_MODEL` env when
  you have a `cfg.embedding_model` available. There's a regression
  test guarding this.
- Don't bypass `safe_pipeline.validate_pipeline` when accepting
  user-supplied aggregation pipelines. Add new blocked stages there,
  not in route handlers.
