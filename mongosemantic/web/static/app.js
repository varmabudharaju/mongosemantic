// mongosemantic web client. Vanilla ES2020+. No build step.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  let CONTENT = {};
  const PAGES = ["connection", "collections", "inspect", "apply", "indexing", "search", "query", "dashboard", "visualize", "mcp", "guide"];

  // MCP tools shown on the MCP page. Source of truth is mcp_server/server.py;
  // this is a static mirror so the UI works without a tool-list endpoint.
  const MCP_TOOLS = [
    ["semantic_search", "Find documents in one collection by meaning."],
    ["hybrid_search", "Combine semantic + BM25 keyword via Atlas $rankFusion. Falls back to pure semantic elsewhere."],
    ["search_all_collections", "Cross-collection fanout, merged by score."],
    ["list_collections", "Every collection with its configured/not-configured status."],
    ["list_configured", "Just the collections with semantic search wired up."],
    ["inspect_collection", "Per-field suitability scoring on a sample."],
    ["get_sample_documents", "A few real documents (embedding sub-doc stripped)."],
    ["get_status", "Topology, configured count, total embeddings, job-queue counts."],
    ["safe_aggregation", "Read-only aggregation runner (10s, 100-row cap, $out/$merge/$function blocked)."],
    ["get_schema_context", "Compact schema summary an AI agent can use to build aggregations."],
    ["migrate_model", "Switch a collection's embedding model with near-zero downtime."],
  ];

  const csrfFromCookie = () => {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };

  async function fetchJson(method, url, body) {
    const opts = {
      method,
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET") {
      opts.headers["X-CSRF-Token"] = csrfFromCookie();
    }
    const r = await fetch(url, opts);
    const text = await r.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    if (!r.ok) {
      const msg = (data && data.detail) || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return data;
  }

  const get = (content, path) => path.split(".").reduce((acc, k) => acc && acc[k], content);

  function hydrateContent(root = document) {
    $$("[data-content]", root).forEach(el => {
      const v = get(CONTENT, el.dataset.content);
      if (v != null) el.textContent = v;
    });
    $$("[data-placeholder]", root).forEach(el => {
      const v = get(CONTENT, el.dataset.placeholder);
      if (v != null) el.placeholder = v;
    });
    $$("[data-default]", root).forEach(el => {
      if (!el.value) el.value = get(CONTENT, el.dataset.default) || "";
    });
  }

  function showPage(name) {
    PAGES.forEach(p => {
      const el = document.getElementById(`page-${p}`);
      if (el) el.hidden = (p !== name);
    });
    $$("#app-nav a").forEach(a => a.toggleAttribute("aria-current", a.dataset.page === name));
    // Auto-close the mobile sidebar after a nav click.
    document.body.classList.remove("sidebar-open");
  }

  function toast(msg) {
    const t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    setTimeout(() => { t.hidden = true; }, 3000);
  }

  const escapeHtml = (s) => String(s ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

  // In-memory store of the currently-displayed sample docs, indexed by the
  // row's data-idx. Lets the click handler retrieve the full doc without
  // re-fetching or round-tripping through HTML.
  let _sampleDocs = [];

  // Render one Mongo document as a compact list row. Shows the first 1-3
  // meaningful fields as a preview; the full doc opens in the side panel on
  // click. Long strings are truncated; arrays/objects get a short tag.
  function renderSampleRow(doc, idx) {
    const entries = Object.entries(doc).filter(([k]) => k !== "_id");
    const preview = entries.slice(0, 3).map(([k, v]) => {
      let display;
      if (v === null) display = "null";
      else if (Array.isArray(v)) display = `[${v.length} item${v.length === 1 ? "" : "s"}]`;
      else if (typeof v === "object") display = v.$oid ? `ObjectId(${v.$oid.slice(0, 6)}…)` : "{…}";
      else if (typeof v === "string") display = v.length > 70 ? v.slice(0, 70) + "…" : v;
      else display = String(v);
      return `<span class="sample-row-field"><span class="sample-row-key">${escapeHtml(k)}:</span> ${escapeHtml(display)}</span>`;
    }).join("");
    return `<button type="button" class="sample-doc-row" data-idx="${idx}">${preview}</button>`;
  }

  function _docLabel(doc) {
    return doc.title || doc.name || doc._id?.$oid?.slice(0, 8) || `Document ${(arguments[1] ?? 0) + 1}`;
  }

  // Generic slide-in detail panel — used by Inspect (sample doc) and
  // Search (result's source_doc). Title + pretty-printed JSON body.
  function openDetailPanel(title, doc) {
    $("#inspect-detail-title").textContent = title;
    $("#inspect-detail-body").textContent = JSON.stringify(doc, null, 2);
    $("#inspect-detail-backdrop").hidden = false;
    const panel = $("#inspect-detail-panel");
    panel.hidden = false;
    panel.setAttribute("aria-hidden", "false");
  }

  function closeInspectDetail() {
    $("#inspect-detail-backdrop").hidden = true;
    const panel = $("#inspect-detail-panel");
    panel.hidden = true;
    panel.setAttribute("aria-hidden", "true");
  }

  function openInspectDetail(idx) {
    const doc = _sampleDocs[idx];
    if (!doc) return;
    openDetailPanel(_docLabel(doc, idx), doc);
  }

  // Wire up the detail-panel handlers once (idempotent on re-runs).
  function _ensureDetailPanelWired() {
    if (_ensureDetailPanelWired._done) return;
    _ensureDetailPanelWired._done = true;
    $("#inspect-detail-close").addEventListener("click", closeInspectDetail);
    $("#inspect-detail-backdrop").addEventListener("click", closeInspectDetail);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#inspect-detail-panel").hidden) closeInspectDetail();
    });
    $("#inspect-sample").addEventListener("click", (e) => {
      const row = e.target.closest(".sample-doc-row");
      if (!row) return;
      openInspectDetail(Number(row.dataset.idx));
    });
  }

  // ---- connection page state machine + actions ---------------------------

  async function renderConnectionPage() {
    // Hide all sub-blocks; the fetch will reveal the right one.
    $("#conn-state-disconnected").hidden = true;
    $("#conn-state-connected").hidden = true;
    $("#conn-banner-env").hidden = true;
    $("#conn-banner-saved").hidden = true;

    let stateRes, pathRes;
    try {
      [stateRes, pathRes] = await Promise.all([
        fetchJson("GET", "/api/connection"),
        fetchJson("GET", "/api/connection/config-path"),
      ]);
    } catch (e) {
      toast("Failed to load connection state: " + e.message);
      return;
    }

    const subtitleKeys = {
      not_connected: "subtitle_disconnected",
      connected_ui:  "subtitle_connected_ui",
      connected_env: "subtitle_connected_env",
    };
    $("#conn-subtitle").textContent =
      CONTENT.connection[subtitleKeys[stateRes.state] || "subtitle_disconnected"];

    renderConnDevHelp(stateRes.env_overrides, pathRes.path);

    if (stateRes.state === "not_connected") {
      $("#conn-state-disconnected").hidden = false;
      wireConnNewForm();
      setNavDisabled(true);
      return;
    }

    setNavDisabled(false);

    $("#conn-state-connected").hidden = false;
    renderConnStatusCard(stateRes);
    wireConnConnectedActions(stateRes);

    if (stateRes.state === "connected_env") {
      $("#conn-banner-env").hidden = false;
      $("#conn-btn-change").hidden = true;
      $("#conn-btn-disconnect").hidden = true;
    } else {
      $("#conn-btn-change").hidden = false;
      $("#conn-btn-disconnect").hidden = false;
    }
  }

  function renderConnStatusCard(state) {
    const c = CONTENT.connection;
    $("#conn-status-title").textContent = `Connected to ${state.database}`;
    const rows = [
      [c.status_label_uri, state.uri_redacted],
      [c.status_label_database, state.database],
      [c.status_label_topology, state.topology || "—"],
      [c.status_label_mongo_version, state.mongo_version || "—"],
      [c.status_label_model, state.model],
      [c.status_label_configured, String(state.configured_count)],
    ];
    const dl = $("#conn-status-rows");
    dl.innerHTML = "";
    for (const [k, v] of rows) {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      dl.appendChild(dt); dl.appendChild(dd);
    }
    // Reset any prior test result.
    const tr = $("#conn-test-result"); tr.hidden = true; tr.className = "conn-test-result"; tr.textContent = "";
  }

  function renderConnDevHelp(overrides, configPath) {
    const c = CONTENT.connection;
    const envDl = $("#conn-devhelp-env");
    envDl.innerHTML = "";
    const labelFor = { uri: "MONGOSEMANTIC_URI", db: "MONGOSEMANTIC_DB", model: "MONGOSEMANTIC_MODEL" };
    for (const key of ["uri", "db", "model"]) {
      const dt = document.createElement("dt"); dt.textContent = labelFor[key];
      const dd = document.createElement("dd");
      dd.textContent = overrides[key] ? c.devhelp_env_yes : c.devhelp_env_no;
      dd.className = overrides[key] ? "env-set" : "env-unset";
      envDl.appendChild(dt); envDl.appendChild(dd);
    }
    $("#conn-devhelp-path").textContent = configPath;
  }

  // Replace a `<input>` element with a `<select>` carrying the same id/name,
  // populated with the given database names. Returns the new element.
  function morphDbInputToSelect(inputId, databases, selected) {
    const old = document.getElementById(inputId);
    if (!old) return null;
    if (old.tagName === "SELECT") {
      // Already a select — just refresh options.
      old.innerHTML = "";
    } else {
      const sel = document.createElement("select");
      sel.id = old.id; sel.name = old.name;
      old.replaceWith(sel);
    }
    const sel = document.getElementById(inputId);
    for (const name of databases) {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      if (name === selected) opt.selected = true;
      sel.appendChild(opt);
    }
    return sel;
  }

  // Fire /api/connection/list-databases for a given URI and update the hint
  // + morph the DB input into a <select>. Returns true on success.
  async function tryPopulateDatabases(uri, dbId, hint) {
    if (!uri.startsWith("mongodb://") && !uri.startsWith("mongodb+srv://")) {
      if (hint) { hint.hidden = false; hint.className = "conn-hint error"; hint.textContent = "URI must start with mongodb:// or mongodb+srv://"; }
      return false;
    }
    if (hint) { hint.hidden = false; hint.className = "conn-hint info"; hint.textContent = "Checking databases…"; }
    let res;
    try { res = await fetchJson("POST", "/api/connection/list-databases", { uri }); }
    catch (err) {
      if (hint) { hint.className = "conn-hint error"; hint.textContent = "Couldn't reach cluster: " + err.message; }
      return false;
    }
    if (!res.ok) {
      if (hint) { hint.className = "conn-hint error"; hint.textContent = `${res.error.message} ${res.error.hint || ""}`.trim(); }
      return false;
    }
    if (!Array.isArray(res.databases) || res.databases.length === 0) {
      if (hint) { hint.className = "conn-hint info"; hint.textContent = "Connected, but no user-visible databases yet — type one to create it on first write."; }
      return false;
    }
    morphDbInputToSelect(dbId, res.databases, res.default);
    if (hint) { hint.className = "conn-hint success"; hint.textContent = `Pick one of ${res.databases.length} database${res.databases.length === 1 ? "" : "s"}.`; }
    return true;
  }

  // Wire URI input -> debounced auto-populate, with blur as a fast-path.
  // Listens on both events because (a) "input" fires on paste, (b) "blur"
  // catches the case where the user pastes via right-click without keystrokes.
  function wireUriBlurPopulator(uriId, dbId, hintId) {
    const uriInput = document.getElementById(uriId);
    if (!uriInput) return;
    const hint = hintId && document.getElementById(hintId);
    let lastUri = "";
    let timer = null;
    const trigger = (uri) => {
      if (!uri || uri === lastUri) return;
      lastUri = uri;
      tryPopulateDatabases(uri, dbId, hint);
    };
    uriInput.addEventListener("input", () => {
      const uri = uriInput.value.trim();
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => trigger(uri), 600);
    });
    uriInput.addEventListener("blur", () => {
      if (timer) { clearTimeout(timer); timer = null; }
      trigger(uriInput.value.trim());
    });
  }

  function wireConnNewForm() {
    const form = $("#conn-form-new");
    const errBox = $("#conn-form-new-error");
    const hint = $("#conn-form-new-hint");
    wireUriBlurPopulator("conn-form-new-uri", "conn-form-new-db", "conn-form-new-hint");
    form.onsubmit = async (e) => {
      e.preventDefault();
      errBox.hidden = true;
      const uri = $("#conn-form-new-uri").value.trim();
      const database = $("#conn-form-new-db").value.trim();
      // If the user hit Connect without picking a database, try to list them
      // first instead of failing with a "database required" error. This
      // catches users who pasted+clicked-Connect with no blur in between.
      if (uri && !database) {
        const populated = await tryPopulateDatabases(uri, "conn-form-new-db", hint);
        if (populated) return;  // user must now pick from the dropdown
        // population failed; fall through and let the server send the friendly error
      }
      let res;
      try { res = await fetchJson("POST", "/api/connection/save", { uri, database }); }
      catch (err) { res = { ok: false, error: { code: "http_error", message: String(err), hint: "", details: "" } }; }
      if (!res.ok) { showConnError(errBox, res.error); return; }
      showSavedBanner(CONTENT.connection.banner_saved.replace("{database}", database));
      renderConnectionPage();
    };
  }

  function wireConnConnectedActions(state) {
    const c = CONTENT.connection;

    $("#conn-btn-test").onclick = async () => {
      const resBox = $("#conn-test-result");
      resBox.hidden = false;
      resBox.className = "conn-test-result";
      resBox.textContent = c.test_running;
      let res;
      try { res = await fetchJson("POST", "/api/connection/test", {}); }
      catch (err) { res = { ok: false, error: { code: "http_error", message: String(err), hint: "", details: "" } }; }
      if (res.ok) {
        resBox.className = "conn-test-result success";
        resBox.textContent = c.test_success
          .replace("{latency_ms}", res.latency_ms)
          .replace("{version}", res.mongo_version);
      } else {
        resBox.className = "conn-test-result error";
        resBox.textContent = `${res.error.message} ${res.error.hint || ""}`.trim();
      }
    };

    $("#conn-btn-change").onclick = () => {
      // Prefill with current values — but uri_redacted has "<redacted>", so blank instead.
      $("#conn-form-change-uri").value = state.uri_redacted.includes("<redacted>") ? "" : state.uri_redacted;
      // Reset DB field to a plain input each time the form opens.
      const dbEl = $("#conn-form-change-db");
      if (dbEl.tagName === "SELECT") {
        const input = document.createElement("input");
        input.id = dbEl.id; input.name = dbEl.name;
        dbEl.replaceWith(input);
      }
      $("#conn-form-change-db").value = state.database;
      $("#conn-form-change").hidden = false;
      wireUriBlurPopulator("conn-form-change-uri", "conn-form-change-db", "conn-form-change-hint");
    };

    $("#conn-form-change-cancel").onclick = () => {
      $("#conn-form-change").hidden = true;
    };

    const changeForm = $("#conn-form-change");
    const changeErr = $("#conn-form-change-error");
    changeForm.onsubmit = async (e) => {
      e.preventDefault();
      changeErr.hidden = true;
      const uri = $("#conn-form-change-uri").value.trim();
      const database = $("#conn-form-change-db").value.trim();
      let res;
      try { res = await fetchJson("POST", "/api/connection/save", { uri, database }); }
      catch (err) { res = { ok: false, error: { code: "http_error", message: String(err), hint: "", details: "" } }; }
      if (!res.ok) { showConnError(changeErr, res.error); return; }
      showSavedBanner(CONTENT.connection.banner_saved.replace("{database}", database));
      renderConnectionPage();
    };

    $("#conn-btn-disconnect").onclick = async () => {
      if (!confirm(c.disconnect_confirm_body)) return;
      let res;
      try { res = await fetchJson("DELETE", "/api/connection"); }
      catch (err) { toast("Disconnect failed: " + err.message); return; }
      if (res.ok) {
        showSavedBanner(c.banner_disconnected);
        renderConnectionPage();
      }
    };
  }

  function showConnError(box, err) {
    box.hidden = false;
    box.innerHTML = `<strong>${escapeHtml(err.message)}</strong>` +
      (err.hint ? `<br>${escapeHtml(err.hint)}` : "") +
      (err.details ? `<details><summary>Show technical details</summary><code>${escapeHtml(err.details)}</code></details>` : "");
  }

  function showSavedBanner(message) {
    const b = $("#conn-banner-saved");
    b.textContent = message;
    b.hidden = false;
  }

  function setNavDisabled(disabled) {
    const tooltip = CONTENT.connection.nav_disabled_tooltip;
    $$("#app-nav a").forEach(a => {
      const page = a.dataset.page;
      if (page === "connection") return;
      if (disabled) {
        a.classList.add("nav-disabled");
        a.setAttribute("aria-disabled", "true");
        a.setAttribute("title", tooltip);
      } else {
        a.classList.remove("nav-disabled");
        a.removeAttribute("aria-disabled");
        a.removeAttribute("title");
      }
    });
  }

  // ---- guide content -----------------------------------------------------
  const GUIDE_HTML = `
    <h3>1. Connection</h3>
    <p>The server already knows where MongoDB lives (from the <code>MONGOSEMANTIC_URI</code> env var). The Connection page lets you point the running server at a different cluster without restarting it.</p>
    <p>Click the topology line on the Dashboard to confirm: <code>atlas</code>, <code>replica_set</code>, or <code>standalone</code> — each unlocks different capabilities.</p>

    <h3>2. Collections</h3>
    <p>Lists every collection in the current database. Each row shows:</p>
    <ul>
      <li><strong>Status</strong> — configured or not.</li>
      <li><strong>Model</strong> — which embedding model is in use, and the storage mode (shadow or inline).</li>
      <li><strong>Inspect</strong> — opens a field-by-field suitability table sampled from the collection.</li>
      <li><strong>Migrate model</strong> — once configured, swap to a different embedding model with near-zero downtime.</li>
    </ul>

    <h4>Apply (configure semantic search)</h4>
    <p>From the Inspect page, click <em>Configure semantic search</em>. You'll pick fields, a storage mode, and a model:</p>
    <ul>
      <li><strong>Shadow</strong> (recommended) — embeddings live in <code>&lt;collection&gt;_embeddings</code>. Your source docs are untouched.</li>
      <li><strong>Inline</strong> — embeddings written onto the source doc at <code>_msem.&lt;field&gt;</code>. Faster reads on Atlas; mutates your docs. Doesn't support chunking.</li>
      <li><strong>Chunked</strong> (shadow only) — long text is split into overlapping chunks before embedding. Search returns the best <em>paragraph</em>, not just the best doc.</li>
    </ul>

    <h4>Index (embed existing docs)</h4>
    <p>After Apply, your existing documents need embeddings. Click <em>Index</em> on the indexing page to enqueue jobs. A background worker processes them — you can watch the queue empty in real time on the Dashboard.</p>

    <h3>3. Search</h3>
    <p>Type a query, hit <strong>Search</strong> or press Enter. Leave the collection dropdown on <em>All configured collections</em> to fan out across every configured collection, or pick one to scope the search.</p>
    <div class="try"><strong>Try:</strong> <code>gear for backpacking trips</code> across all collections. You should see results from both <code>articles</code> (a Southeast Asia backpacking post) and <code>products</code> (a 60L pack, hiking boots).</div>
    <p><strong>Hybrid</strong> toggle: combines semantic similarity with BM25 keyword matching via Atlas <code>$rankFusion</code>. Useful when a query has both fuzzy meaning and a specific term (a product code, a version number). Requires Atlas + shadow mode; falls back to pure semantic everywhere else with a banner explaining why.</p>

    <h3>4. Visualize</h3>
    <p>Sampled embeddings projected to 2D via PCA. Points close together are similar by meaning. Hover any point for the source snippet.</p>
    <div class="try"><strong>Try:</strong> pick <code>articles</code>. If you've seeded the demo data, you should see distinct clusters for travel, programming, cooking, fitness, finance — each a separate visual blob.</div>

    <h3>5. Query (safe aggregation)</h3>
    <p>Run read-only MongoDB aggregation pipelines against any collection. Pipeline runs with a 10-second cap and 100-row limit. <code>$out</code>, <code>$merge</code>, <code>$function</code>, <code>$accumulator</code>, <code>$where</code>, and <code>$jsonSchema</code> are blocked at parse time — the response tells you exactly why.</p>
    <div class="try"><strong>Try</strong> on <code>articles</code>:<br>
      <code>[{"$group": {"_id": "$category", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]</code><br>
      Returns counts per category.
    </div>

    <h3>6. Dashboard</h3>
    <p>Single-page overview: topology, total embeddings, pending and failed jobs, plus the most recent failures with the actual error message. The <strong>Workers</strong> section shows every worker that's heartbeated recently — running, stale, or dead.</p>
    <p>Start a worker from a terminal to see the workers section come alive:</p>
    <div class="code-block"><code>mongosemantic worker</code></div>

    <h3>7. Migrate model (live demo)</h3>
    <p>On the Collections page, click <strong>Migrate model</strong> on any shadow-mode row. Pick a different model, click Migrate, and watch the progress bar. The old shadow collection is preserved as <code>&lt;name&gt;_archive_&lt;timestamp&gt;</code> for rollback — drop it once you've verified.</p>
    <p>Search keeps serving the old model right up to the swap instant, then the new model from there on. The window between cfg update and atomic <code>renameCollection</code> is milliseconds.</p>

    <h3>8. MCP (AI agent integration)</h3>
    <p>The <strong>MCP</strong> page in the nav has the exact command to wire mongosemantic into Claude Desktop. Once integrated, any chat in Claude can invoke any of the 11 tools (<code>semantic_search</code>, <code>hybrid_search</code>, <code>safe_aggregation</code>, etc.).</p>

    <h3>What's not in the UI</h3>
    <ul>
      <li><strong>Atlas-only paths</strong> (real <code>$vectorSearch</code>, <code>$rankFusion</code>, automatic Atlas Search index creation) need an Atlas cluster. See <code>docs/atlas-setup.md</code> in the repo for a free-tier runbook.</li>
      <li><strong>Reindex</strong> and <strong>retry failed jobs</strong> exist as API endpoints and CLI commands but aren't surfaced as buttons here yet (besides the Retry-all on the dashboard).</li>
    </ul>
  `;

  // ---- visualize scatter -------------------------------------------------
  let _vizPoints = [];
  function drawScatter(points) {
    _vizPoints = points;
    const canvas = $("#viz-canvas");
    if (!canvas) return;
    // Snap canvas pixel dims to CSS dims for crisp drawing.
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.fillStyle = "rgba(0,104,74,0.55)";
    ctx.strokeStyle = "rgba(0,30,43,0.6)";
    ctx.lineWidth = 0.5;
    const pad = 24;
    const w = rect.width - 2 * pad, h = rect.height - 2 * pad;
    points.forEach(p => {
      const cx = pad + p.x * w;
      const cy = pad + (1 - p.y) * h;  // flip y so larger PCA-y = up
      ctx.beginPath();
      ctx.arc(cx, cy, 3.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    });
    // Tooltip on hover.
    const tooltip = $("#viz-tooltip");
    canvas.onmousemove = (ev) => {
      const r = canvas.getBoundingClientRect();
      const mx = ev.clientX - r.left, my = ev.clientY - r.top;
      let nearest = null, nearestDist = 12; // px threshold
      for (const p of _vizPoints) {
        const cx = pad + p.x * (r.width - 2 * pad);
        const cy = pad + (1 - p.y) * (r.height - 2 * pad);
        const d = Math.hypot(cx - mx, cy - my);
        if (d < nearestDist) { nearest = { p, cx, cy }; nearestDist = d; }
      }
      if (nearest) {
        tooltip.style.display = "block";
        tooltip.style.left = (nearest.cx + 14) + "px";
        tooltip.style.top  = (nearest.cy + 14) + "px";
        tooltip.textContent = nearest.p.text || nearest.p.id;
      } else {
        tooltip.style.display = "none";
      }
    };
    canvas.onmouseleave = () => { tooltip.style.display = "none"; };
  }

  // ---- migrate modal ----------------------------------------------------
  let _migratePollTimer = null;
  function openMigrateModal(name, mode) {
    const modal = $("#migrate-modal");
    $("#migrate-target").textContent =
      `${name} — ${mode === "inline" ? "inline mode is not supported for migration" : "shadow mode, near-zero downtime"}`;
    $("#migrate-progress").style.display = "none";
    $("#migrate-progress").value = 0;
    $("#migrate-state").textContent = "";
    $("#migrate-go").disabled = (mode === "inline");
    modal.hidden = false;

    $("#migrate-cancel").onclick = () => {
      if (_migratePollTimer) clearInterval(_migratePollTimer);
      modal.hidden = true;
    };
    $("#migrate-go").onclick = async () => {
      const model = $("#migrate-model").value;
      const drop = $("#migrate-drop").checked;
      $("#migrate-go").disabled = true;
      $("#migrate-progress").style.display = "block";
      $("#migrate-state").textContent = "starting…";
      try {
        await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/migrate`,
          { model, drop_archive: drop, background: true });
      } catch (e) {
        $("#migrate-state").textContent = "failed: " + e.message;
        $("#migrate-go").disabled = false;
        return;
      }
      if (_migratePollTimer) clearInterval(_migratePollTimer);
      _migratePollTimer = setInterval(async () => {
        try {
          const p = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/migrate/progress`);
          if (p.total > 0) {
            $("#migrate-progress").max = p.total;
            $("#migrate-progress").value = p.processed || 0;
          }
          $("#migrate-state").textContent =
            `${p.state} · ${p.processed || 0}/${p.total || "?"}` + (p.target_model ? ` → ${p.target_model}` : "");
          if (p.state === "succeeded" || p.state === "failed") {
            clearInterval(_migratePollTimer);
            _migratePollTimer = null;
            if (p.state === "succeeded") {
              toast(`Migrated ${name} to ${p.new_model}.`);
              setTimeout(() => { modal.hidden = true; handlers.collections(); }, 600);
            } else {
              $("#migrate-state").textContent = "failed: " + (p.error || "unknown");
              $("#migrate-go").disabled = false;
            }
          }
        } catch (e) {
          $("#migrate-state").textContent = "poll error: " + e.message;
        }
      }, 700);
    };
  }

  const route = () => {
    const hash = (location.hash || "#/connection").replace(/^#\//, "").split("/");
    const [page, ...args] = hash;
    if (!PAGES.includes(page)) { location.hash = "#/connection"; return; }
    // Clean up any leftover collection tabs before the new handler runs;
    // handlers that need them call mountCollectionTabs() themselves.
    document.querySelectorAll(".collection-tabs").forEach(el => el.remove());
    showPage(page);
    handlers[page] && handlers[page](args.map(decodeURIComponent));
  };

  // Shared tab strip rendered at the top of any page that's scoped to a
  // single collection. Lets users jump between Inspect / Configure / Index
  // / Search without losing the collection they were working on.
  const COLLECTION_TABS = [
    { page: "inspect",  label: "Inspect"   },
    { page: "apply",    label: "Configure" },
    { page: "indexing", label: "Index"     },
    { page: "search",   label: "Search"    },
  ];
  function mountCollectionTabs(collection, currentPage) {
    document.querySelectorAll(".collection-tabs").forEach(el => el.remove());
    if (!collection) return;
    const section = $(`#page-${currentPage}`);
    if (!section) return;
    const n = encodeURIComponent(collection);
    const links = COLLECTION_TABS.map(t => {
      const active = t.page === currentPage ? ' aria-current="page"' : "";
      return `<a href="#/${t.page}/${n}"${active}>${t.label}</a>`;
    }).join("");
    const nav = document.createElement("nav");
    nav.className = "collection-tabs";
    nav.innerHTML = `<span class="collection-tabs-context">Collection · <strong>${escapeHtml(collection)}</strong></span>${links}`;
    // Insert at the very top of the section, before the h2.
    section.insertBefore(nav, section.firstChild);
  }

  const handlers = {
    connection() { renderConnectionPage(); },

    collections: async () => {
      const tbl = $("#collections-table");
      tbl.innerHTML = "";
      $("#collections-empty").hidden = true;
      try {
        const data = await fetchJson("GET", "/api/collections");
        if (!data.collections.length) {
          $("#collections-empty").hidden = false;
          return;
        }
        const head = `<thead><tr>
          <th>${escapeHtml(CONTENT.collections.col_collection)}</th>
          <th>${escapeHtml(CONTENT.collections.col_status)}</th>
          <th>Model</th>
          <th></th>
        </tr></thead>`;
        const rows = data.collections.map(c => {
          const isConf = c.status === "configured";
          const pill = `<span class="status ${isConf ? "configured" : ""}">${
            isConf
              ? escapeHtml(CONTENT.collections.status_configured.replace("{n}", c.fields_count))
              : escapeHtml(CONTENT.collections.status_not_configured)
          }</span>`;
          const model = c.embedding_model
            ? `<code style="font-size:12px">${escapeHtml(c.embedding_model)}</code> <small>(${escapeHtml(c.mode || "")})</small>`
            : `<small>—</small>`;
          const n = encodeURIComponent(c.name);
          const actions = isConf
            ? `<a href="#/inspect/${n}">Inspect</a>
               &nbsp;·&nbsp;
               <a href="#/apply/${n}">Reconfigure</a>
               &nbsp;·&nbsp;
               <a href="#" data-reindex="${escapeHtml(c.name)}">Reindex</a>
               &nbsp;·&nbsp;
               <a href="#" data-migrate="${escapeHtml(c.name)}" data-mode="${escapeHtml(c.mode || "")}">Migrate</a>
               &nbsp;·&nbsp;
               <a href="#" data-teardown="${escapeHtml(c.name)}" style="color:var(--mdb-bad)">Remove</a>`
            : `<a href="#/inspect/${n}">${escapeHtml(CONTENT.collections.row_action)}</a>`;
          return `<tr>
            <td><strong>${escapeHtml(c.name)}</strong></td>
            <td>${pill}</td>
            <td>${model}</td>
            <td style="text-align:right;white-space:nowrap">${actions}</td>
          </tr>`;
        }).join("");
        tbl.innerHTML = head + "<tbody>" + rows + "</tbody>";
        // Wire the per-row actions.
        $$("a[data-migrate]", tbl).forEach(a => {
          a.onclick = (ev) => { ev.preventDefault(); openMigrateModal(a.dataset.migrate, a.dataset.mode); };
        });
        $$("a[data-reindex]", tbl).forEach(a => {
          a.onclick = async (ev) => {
            ev.preventDefault();
            const name = a.dataset.reindex;
            if (!confirm(`Reindex ${name}? This drops existing embeddings and re-enqueues every doc.`)) return;
            try {
              const r = await fetchJson("POST", "/api/reindex", { collection: name });
              toast(`Enqueued ${r.enqueued} reindex job(s).`);
            } catch (e) { toast(e.message); }
          };
        });
        $$("a[data-teardown]", tbl).forEach(a => {
          a.onclick = async (ev) => {
            ev.preventDefault();
            const name = a.dataset.teardown;
            if (!confirm(`Remove semantic-search config from ${name}?\n\nThis drops the shadow collection (or clears inline _msem) and deletes the config. Cannot be undone — but you can apply again.`)) return;
            try {
              await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/teardown`, { drop_data: true });
              toast(`Removed config for ${name}.`);
              handlers.collections();
            } catch (e) { toast(e.message); }
          };
        });
      } catch (e) { toast(e.message); }
    },

    inspect: async ([name]) => {
      if (!name) return;
      mountCollectionTabs(name, "inspect");
      $("#inspect-title").textContent = CONTENT.inspect.title.replace("{collection}", name);
      $("#inspect-apply-link").href = `#/apply/${encodeURIComponent(name)}`;
      _ensureDetailPanelWired();
      closeInspectDetail();  // reset panel state when switching collections

      // Sample documents — scrollable list at the bottom of the page. Each
      // row opens the full doc in the side panel on click.
      const sample = $("#inspect-sample");
      sample.innerHTML = '<p class="sample-doc-empty">loading sample…</p>';
      try {
        const s = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/sample?limit=20`);
        _sampleDocs = s.documents || [];
        sample.innerHTML = _sampleDocs.length
          ? _sampleDocs.map((d, i) => renderSampleRow(d, i)).join("")
          : '<p class="sample-doc-empty">No documents in this collection.</p>';
      } catch (e) {
        _sampleDocs = [];
        sample.innerHTML = `<p class="sample-doc-empty">Could not load sample: ${escapeHtml(e.message)}</p>`;
      }

      // Field analysis table.
      try {
        const data = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/inspect`);
        $("#inspect-subtitle").textContent =
          CONTENT.inspect.subtitle.replace("{n}", data.sample_size);
        const head = `<thead><tr>
          <th>${escapeHtml(CONTENT.inspect.col_field)}</th>
          <th>${escapeHtml(CONTENT.inspect.col_type)}</th>
          <th>${escapeHtml(CONTENT.inspect.col_coverage)}</th>
          <th>${escapeHtml(CONTENT.inspect.col_avg_length)}</th>
          <th>${escapeHtml(CONTENT.inspect.col_suitability)}</th>
        </tr></thead>`;
        const rows = data.fields.map(f => `<tr>
          <td>${escapeHtml(f.path)}</td>
          <td>${escapeHtml(f.type)}</td>
          <td>${(f.coverage * 100).toFixed(0)}%</td>
          <td>${f.avg_len}</td>
          <td><span class="band band-${escapeHtml(f.band)}">${escapeHtml(CONTENT.inspect["band_" + f.band])}</span></td>
        </tr>`).join("");
        $("#inspect-table").innerHTML = head + "<tbody>" + rows + "</tbody>";
      } catch (e) { toast(e.message); }
    },

    apply: async ([name]) => {
      if (!name) { toast("Pick a collection from Collections first."); return; }
      mountCollectionTabs(name, "apply");
      const f = $("#form-apply");
      const c = CONTENT.apply;
      // Try to load existing config — present? then this is a Reconfigure.
      let existing = null;
      try {
        const cfg = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/config`);
        if (cfg.configured) existing = cfg;
      } catch { /* no config yet; new apply */ }
      const isReconfigure = !!existing;
      // Load the inspected field list so we can show checkboxes instead of
      // a free-form text input. Falls back to a text input if inspect fails.
      let inspected = null;
      try {
        inspected = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/inspect`);
      } catch { /* fall through to text input fallback below */ }
      const titleEl = document.querySelector("#page-apply h2");
      if (titleEl) titleEl.textContent = isReconfigure
        ? `Reconfigure ${name}` : `Configure ${name}`;
      const submitLabel = isReconfigure ? "Save changes" : escapeHtml(c.cta_apply);
      const initialFields  = isReconfigure ? existing.fields : [];
      const initialMode    = isReconfigure ? existing.mode : "shadow";
      const initialChunked = isReconfigure ? existing.chunked : false;
      const initialModel   = isReconfigure ? existing.model : "local-fast";
      const modelOptions = [
        ["local-fast",   c.model_local_fast],
        ["local-better", c.model_local_better],
        ["openai-small", c.model_openai_small],
        ["openai-large", c.model_openai_large],
        ["ollama-nomic", c.model_ollama_nomic],
      ].map(([val, label]) =>
        `<option value="${val}" ${val === initialModel ? "selected" : ""}>${escapeHtml(label)}</option>`
      ).join("");
      // Fields UI: checkboxes if /inspect succeeded, else a comma-list input.
      let fieldsBlock;
      if (inspected && Array.isArray(inspected.fields) && inspected.fields.length) {
        const eligible = inspected.fields.filter(x => x.band !== "not_recommended");
        if (!eligible.length) {
          fieldsBlock = `<p class="apply-field-empty">No text-shaped fields were detected in this collection. Embed something else first.</p>`;
        } else {
          const initialSet = new Set(initialFields);
          fieldsBlock = `<div class="apply-field-list">` +
            eligible.map(field => {
              const checked = initialSet.has(field.path) ? "checked" : "";
              const bandLabel = CONTENT.inspect["band_" + field.band] || field.band;
              return `<label class="apply-field-row">
                <input type="checkbox" name="apply-field" value="${escapeHtml(field.path)}" ${checked}>
                <code>${escapeHtml(field.path)}</code>
                <span class="band band-${escapeHtml(field.band)}">${escapeHtml(bandLabel)}</span>
              </label>`;
            }).join("") +
            `</div>`;
        }
      } else {
        // Fallback if inspect failed: original free-form input.
        fieldsBlock = `<input id="apply-fields" placeholder="comma-separated paths, e.g. body, title" value="${escapeHtml(initialFields.join(", "))}">`;
      }
      f.innerHTML = `
        <fieldset>
          <legend>${escapeHtml(c.section_fields)}</legend>
          ${fieldsBlock}
        </fieldset>
        <fieldset>
          <legend>${escapeHtml(c.section_mode)}</legend>
          <label><input type="radio" name="mode" value="shadow" ${initialMode === "shadow" ? "checked" : ""}> ${escapeHtml(c.mode_shadow)}</label>
          <br>
          <label><input type="radio" name="mode" value="inline" ${initialMode === "inline" ? "checked" : ""}> ${escapeHtml(c.mode_inline)}</label>
        </fieldset>
        <fieldset>
          <legend>${escapeHtml(c.section_chunking)}</legend>
          <label><input id="apply-chunked" type="checkbox" ${initialChunked ? "checked" : ""}> ${escapeHtml(c.chunking_toggle)}</label>
          <small>${escapeHtml(c.mode_chunk_notice)}</small>
        </fieldset>
        <fieldset>
          <legend>${escapeHtml(c.section_model)}</legend>
          <select id="apply-model">${modelOptions}</select>
          <small style="display:block;margin-top:6px">Changing the model from the current value requires a migration, not just a reconfigure. Use the Migrate action on the Collections page instead.</small>
        </fieldset>
        <button type="submit" class="cta-button" style="border:none;cursor:pointer">${submitLabel}</button>
        ${isReconfigure ? `<p style="margin-top:12px;font-size:13px;color:var(--mdb-ink-muted)">After saving, click <em>Reindex</em> on the Collections page to clear and re-embed with the new field set.</p>` : ""}
      `;
      f.onsubmit = async ev => {
        ev.preventDefault();
        // Read selected fields from checkboxes if present, else from the
        // fallback text input.
        let fields;
        const checks = f.querySelectorAll('input[name="apply-field"]:checked');
        if (checks.length || f.querySelector('input[name="apply-field"]')) {
          fields = Array.from(checks).map(el => el.value);
        } else {
          fields = $("#apply-fields").value.split(",").map(s => s.trim()).filter(Boolean);
        }
        if (!fields.length) { toast("Pick at least one field to embed."); return; }
        const mode = (f.querySelector('input[name="mode"]:checked') || {}).value || "shadow";
        const chunked = $("#apply-chunked").checked;
        const model = $("#apply-model").value;
        try {
          await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/apply`,
            { fields, mode, chunked, model });
          toast(CONTENT.global.toast_config_updated);
          // After a fresh apply send the user to indexing. After a
          // reconfigure leave them on Collections so they can reindex
          // when they're ready.
          location.hash = isReconfigure ? "#/collections" : `#/indexing/${encodeURIComponent(name)}`;
        } catch (e) { toast(e.message); }
      };
    },

    indexing: async ([name]) => {
      if (!name) return;
      mountCollectionTabs(name, "indexing");
      $("#indexing-title").textContent = CONTENT.indexing.title.replace("{collection}", name);
      const bar = $("#indexing-progress");
      const metric = $("#indexing-metric");
      bar.max = 1; bar.value = 0;
      metric.textContent = "Enqueueing…";
      try {
        const r = await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/index`);
        // Enqueueing is synchronous and complete by the time this returns.
        // Show the progress bar as full + a clear count + worker hint.
        bar.max = 1; bar.value = 1;
        const jobs = r.enqueued || 0;
        const docs = r.total || 0;
        const c = CONTENT.indexing;
        metric.innerHTML =
          escapeHtml(c.metric_enqueued
            .replace("{jobs}", jobs)
            .replace("{s}", jobs === 1 ? "" : "s")
            .replace("{docs}", docs)
            .replace("{ds}", docs === 1 ? "" : "s")) +
          `<br><small style="color:var(--mdb-ink-muted)">${escapeHtml(c.metric_worker_hint)}</small>`;
        toast(c.toast_complete.replace("{n}", jobs));
      } catch (e) { toast(e.message); }
    },

    search: async ([scopedCollection]) => {
      mountCollectionTabs(scopedCollection, "search");
      _ensureDetailPanelWired();
      closeInspectDetail();
      const c = CONTENT.search;
      const empty = $("#search-empty");
      const results = $("#search-results");
      const notice = $("#search-notice");
      const stats = $("#search-stats");
      empty.textContent = c.empty_no_query;
      results.innerHTML = "";
      notice.textContent = "";
      stats.hidden = true;
      const sel = $("#search-collection");
      const hybridBox = $("#search-hybrid");
      const limitInput = $("#search-limit");
      const minScoreInput = $("#search-min-score");
      const limitValue = $("#search-limit-value");
      const minScoreValue = $("#search-min-score-value");
      // Sync slider value labels live.
      limitInput.oninput = () => { limitValue.textContent = limitInput.value; };
      minScoreInput.oninput = () => {
        minScoreValue.textContent = Number(minScoreInput.value).toFixed(2);
      };
      try {
        const cols = await fetchJson("GET", "/api/collections");
        sel.innerHTML = `<option value="">${escapeHtml(c.selector_all)}</option>` +
          cols.collections.filter(x => x.status === "configured")
            .map(x => `<option value="${escapeHtml(x.name)}">${escapeHtml(x.name)}</option>`).join("");
        if (scopedCollection) sel.value = scopedCollection;
      } catch { /* leave empty */ }
      const input = $("#search-q");
      input.placeholder = c.placeholder;

      // Holds the rows from the last run so the click→detail handler can
      // recover the full source_doc by index without round-tripping.
      let _searchRows = [];
      const onResultClick = (e) => {
        const li = e.target.closest("li[data-idx]");
        if (!li) return;
        const row = _searchRows[Number(li.dataset.idx)];
        if (!row) return;
        const title = (row.source_doc && (row.source_doc.title || row.source_doc.name))
          || `${row.source_collection} · ${row.field_path}`;
        openDetailPanel(title, row.source_doc || row);
      };
      results.onclick = onResultClick;

      const run = async () => {
        const q = input.value.trim();
        if (!q) {
          results.innerHTML = ""; notice.textContent = "";
          stats.hidden = true;
          empty.textContent = c.empty_no_query; return;
        }
        const goBtn = $("#search-go");
        const origLabel = goBtn.textContent;
        goBtn.textContent = "Searching…";
        goBtn.disabled = true;
        const params = new URLSearchParams({ q, limit: limitInput.value });
        if (sel.value) params.set("collection", sel.value);
        if (hybridBox.checked) params.set("hybrid", "true");
        if (Number(minScoreInput.value) > 0) params.set("min_score", minScoreInput.value);
        try {
          const r = await fetchJson("GET", "/api/search?" + params.toString());
          notice.textContent = r.notice || "";
          _searchRows = r.rows || [];
          if (!_searchRows.length) {
            empty.textContent = c.empty_no_results;
            results.innerHTML = "";
            stats.hidden = false;
            stats.textContent = `0 results in ${r.took_ms ?? "—"} ms` +
              (Number(minScoreInput.value) > 0
                ? ` — try lowering the min-score threshold (${Number(minScoreInput.value).toFixed(2)})`
                : "");
            return;
          }
          empty.textContent = "";
          const scores = _searchRows.map(x => x.score || 0);
          const top = Math.max(...scores), bot = Math.min(...scores);
          stats.hidden = false;
          stats.textContent =
            `${_searchRows.length} result${_searchRows.length === 1 ? "" : "s"} ` +
            `in ${r.took_ms ?? "—"} ms · scores ${bot.toFixed(3)}–${top.toFixed(3)} · ` +
            `click any row to inspect the full document`;
          results.innerHTML = _searchRows.map((row, idx) => {
            const score = row.score || 0;
            // Visual bar — score is already cosine-similarity-ish (0..1).
            const barPct = Math.max(0, Math.min(100, score * 100));
            return `<li data-idx="${idx}">
              <strong>${score.toFixed(3)}</strong>
              <div>
                <div class="meta">
                  <span class="coll">${escapeHtml(row.source_collection)}</span>
                  <span>·</span>
                  <span>${escapeHtml(row.field_path)}</span>
                  ${row.chunk_index !== null && row.chunk_index !== undefined
                    ? `<span>·</span><span>chunk ${row.chunk_index}</span>` : ""}
                </div>
                <div class="search-score-bar"><span style="width:${barPct}%"></span></div>
                <p>${escapeHtml((row.chunk_text || "").slice(0, 300))}</p>
              </div>
            </li>`;
          }).join("");
        } catch (e) {
          toast(e.message);
        } finally {
          goBtn.textContent = origLabel;
          goBtn.disabled = false;
        }
      };
      // Form submit handles both Enter-in-input and Search button click.
      $("#search-form").onsubmit = (ev) => { ev.preventDefault(); run(); };
      // Re-run on filter change ONLY if the user already typed something.
      const rerunIfQuery = () => { if (input.value.trim()) run(); };
      sel.onchange = rerunIfQuery;
      hybridBox.onchange = rerunIfQuery;
      // Debounce slider re-runs so dragging doesn't spam the server.
      let _filterTimer = 0;
      const debouncedRerun = () => {
        clearTimeout(_filterTimer);
        _filterTimer = setTimeout(rerunIfQuery, 250);
      };
      limitInput.addEventListener("change", debouncedRerun);
      minScoreInput.addEventListener("change", debouncedRerun);
      input.focus();
    },

    query: async () => {
      const ta = $("#query-pipeline");
      const out = $("#query-results");
      const sel = $("#query-collection");
      try {
        const cols = await fetchJson("GET", "/api/collections");
        sel.innerHTML = cols.collections
          .map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}${c.status === "configured" ? " (configured)" : ""}</option>`)
          .join("");
      } catch (e) { toast(e.message); }
      $("#query-run").onclick = async () => {
        let pipeline;
        try { pipeline = JSON.parse(ta.value); }
        catch { toast(CONTENT.aggregation.error_rejected.replace("{reason}", "invalid JSON")); return; }
        const collection = sel.value;
        if (!collection) { toast("collection is required"); return; }
        try {
          const r = await fetchJson(
            "POST",
            `/api/collections/${encodeURIComponent(collection)}/aggregation`,
            { pipeline },
          );
          out.textContent = JSON.stringify(r.rows, null, 2);
        } catch (e) {
          out.textContent = CONTENT.aggregation.error_rejected.replace("{reason}", e.message);
        }
      };
    },

    dashboard: async () => {
      const renderOnce = async () => {
      try {
        const d = await fetchJson("GET", "/api/dashboard");
        const card = (label, value, sub, accent) => `
          <div>
            <span class="label">${escapeHtml(label)}</span>
            <span class="value ${accent ? "accent" : ""}">${escapeHtml(String(value))}</span>
            ${sub ? `<span class="sublabel">${escapeHtml(sub)}</span>` : ""}
          </div>`;
        $("#dashboard-cards").innerHTML = [
          card(CONTENT.dashboard.card_collections, d.configured_count, "", true),
          card(CONTENT.dashboard.card_total_embeddings, d.total_embeddings, "", true),
          card(CONTENT.dashboard.card_pending, d.jobs.pending || 0,
               (d.jobs.pending || 0) > 0 ? "waiting on worker" : "queue clear"),
          card(CONTENT.dashboard.card_failed, d.jobs.failed || 0,
               (d.jobs.failed || 0) > 0 ? "retry from below" : "no failures"),
          card("Topology", d.topology.replace("_", " "), `${d.configured.length} configured`),
        ].join("");

        // Per-collection indexing breakdown
        const perTbl = $("#dashboard-perCollection");
        const byColl = d.jobs_by_collection || {};
        const collNames = Object.keys(byColl).sort();
        if (collNames.length === 0) {
          perTbl.innerHTML = `<tbody><tr><td><small>No jobs in the queue. Once you index a collection, activity shows here.</small></td></tr></tbody>`;
        } else {
          perTbl.innerHTML = `<thead><tr>
            <th>Collection</th>
            <th style="text-align:right">Pending</th>
            <th style="text-align:right">In flight</th>
            <th style="text-align:right">Completed</th>
            <th style="text-align:right">Failed</th>
            <th style="text-align:right">Progress</th>
          </tr></thead><tbody>` + collNames.map(name => {
            const c = byColl[name];
            const pending = c.pending || 0;
            const inflight = c.in_flight || 0;
            const completed = c.completed || 0;
            const failed = c.failed || 0;
            const total = pending + inflight + completed + failed;
            const pct = total ? Math.round((completed / total) * 100) : 0;
            return `<tr>
              <td><strong>${escapeHtml(name)}</strong></td>
              <td style="text-align:right">${pending}</td>
              <td style="text-align:right">${inflight ? `<span style="color:var(--mdb-forest);font-weight:600">${inflight}</span>` : 0}</td>
              <td style="text-align:right">${completed}</td>
              <td style="text-align:right">${failed ? `<span style="color:var(--mdb-bad)">${failed}</span>` : 0}</td>
              <td style="text-align:right;min-width:140px">
                <div style="display:inline-flex;align-items:center;gap:8px">
                  <div style="width:100px;height:6px;background:var(--mdb-line);border-radius:3px;overflow:hidden">
                    <div style="width:${pct}%;height:100%;background:linear-gradient(90deg,var(--mdb-forest),var(--mdb-leaf));transition:width 200ms"></div>
                  </div>
                  <small style="min-width:30px">${pct}%</small>
                </div>
              </td>
            </tr>`;
          }).join("") + `</tbody>`;
        }

        // Workers
        const wTbl = $("#dashboard-workers");
        if ((d.workers || []).length === 0) {
          wTbl.innerHTML = `<tbody><tr><td><small>No workers seen recently. Run <code>mongosemantic worker</code>.</small></td></tr></tbody>`;
        } else {
          wTbl.innerHTML = `<thead><tr>
            <th>Worker</th><th>Status</th><th>Last heartbeat</th><th style="text-align:right">Jobs</th>
          </tr></thead><tbody>` + d.workers.map(w => `<tr>
            <td><code style="font-size:12px">${escapeHtml(w.worker_id)}</code></td>
            <td><span class="status ${w.status === "running" ? "configured" : ""}">${escapeHtml(w.status)}</span></td>
            <td><small>${escapeHtml(w.last_heartbeat.replace("T", " ").slice(0, 19))}</small></td>
            <td style="text-align:right">${w.jobs_processed}</td>
          </tr>`).join("") + `</tbody>`;
        }

        // Failed jobs
        const failed = d.recent_failed || [];
        const failedEl = $("#dashboard-failed");
        if (failed.length === 0) {
          failedEl.innerHTML = `<p style="color:var(--mdb-ink-muted);font-size:13px">No recent failures.</p>`;
        } else {
          failedEl.innerHTML = `<div class="table-wrap"><table>
            <thead><tr><th>Collection</th><th>Field</th><th>Source</th><th>Attempts</th><th>Error</th></tr></thead>
            <tbody>${failed.map(f => `<tr>
              <td>${escapeHtml(f.collection || "-")}</td>
              <td>${escapeHtml(f.field_path || "-")}</td>
              <td><code style="font-size:12px">${escapeHtml((f.source_id || "-").toString().slice(0, 24))}</code></td>
              <td>${f.attempts || 0}</td>
              <td><small>${escapeHtml((f.last_error || "").split("\\n")[0].slice(0, 120))}</small></td>
            </tr>`).join("")}</tbody>
          </table></div>`;
        }
      } catch (e) { /* swallow — auto-poll keeps retrying */ }
      };  // end renderOnce
      await renderOnce();
      // Auto-refresh while the user stays on this page.
      if (window._dashboardTimer) clearInterval(window._dashboardTimer);
      window._dashboardTimer = setInterval(() => {
        if (location.hash.startsWith("#/dashboard")) renderOnce();
        else { clearInterval(window._dashboardTimer); window._dashboardTimer = null; }
      }, 3000);
      $("#dashboard-retry").onclick = async () => {
        try {
          const r = await fetchJson("POST", "/api/jobs/retry");
          toast(`reset ${r.reset} failed job(s)`);
          renderOnce();
        } catch (e) { toast(e.message); }
      };
    },

    mcp: () => {
      // Populate the tools table. Copy buttons get wired up here too.
      const tbl = $("#mcp-tools-table");
      if (tbl) {
        tbl.innerHTML = `<thead><tr><th>Tool</th><th>What it does</th></tr></thead><tbody>` +
          MCP_TOOLS.map(([name, desc]) => `<tr>
            <td><code style="font-size:12px">${escapeHtml(name)}</code></td>
            <td>${escapeHtml(desc)}</td>
          </tr>`).join("") + `</tbody>`;
      }
      $$(".copy-btn").forEach(btn => {
        btn.onclick = async () => {
          const key = btn.dataset.copy;
          const val = get(CONTENT, key) || "";
          try {
            await navigator.clipboard.writeText(val);
            const orig = btn.textContent;
            btn.textContent = "Copied";
            setTimeout(() => { btn.textContent = orig; }, 1200);
          } catch {
            toast("clipboard unavailable");
          }
        };
      });
    },

    guide: () => {
      $("#guide-content").innerHTML = GUIDE_HTML;
    },

    visualize: async () => {
      const sel = $("#viz-collection");
      const fieldSel = $("#viz-field");
      const meta = $("#viz-meta");
      const empty = $("#viz-empty");
      try {
        const cols = await fetchJson("GET", "/api/collections");
        const configured = cols.collections.filter(c => c.status === "configured");
        if (!configured.length) {
          empty.textContent = "No collections configured yet — set one up first.";
          sel.innerHTML = "";
          return;
        }
        empty.textContent = "";
        sel.innerHTML = configured.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("");
      } catch (e) { toast(e.message); return; }

      const render = async () => {
        const name = sel.value;
        if (!name) return;
        const params = new URLSearchParams();
        if (fieldSel.value) params.set("field", fieldSel.value);
        params.set("sample", "1000");
        try {
          const v = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/visualize?${params}`);
          // Populate field dropdown (only when first loading or after collection change)
          if (fieldSel.dataset.collection !== name) {
            fieldSel.dataset.collection = name;
            fieldSel.innerHTML = (v.available_fields || []).map(
              f => `<option value="${escapeHtml(f)}" ${f === v.field ? "selected" : ""}>${escapeHtml(f)}</option>`
            ).join("");
          }
          if (v.message) {
            meta.textContent = v.message;
            drawScatter([]);
            return;
          }
          meta.textContent = `${v.points.length} points · ${v.embedding_dim}-d → PCA to 2D`;
          drawScatter(v.points);
        } catch (e) { toast(e.message); }
      };
      sel.onchange = () => { fieldSel.dataset.collection = ""; render(); };
      fieldSel.onchange = render;
      $("#viz-refresh").onclick = render;
      await render();
    },
    mcp() { },
  };

  // Bootstrap
  (async () => {
    try {
      CONTENT = await fetchJson("GET", "/api/content");
      hydrateContent();
    } catch (e) {
      console.error("content load failed", e);
    }
    try {
      const v = await fetchJson("GET", "/api/version");
      const el = $("#sidebar-version");
      if (el && v && v.version) el.textContent = "v" + v.version;
    } catch { /* sidebar footer just stays as the placeholder */ }

    // Global queue indicator — visible from any page, every 5s.
    // Shows: progress fraction + percent while work pending or in-flight,
    // worker liveness via dot color (green=fresh heartbeat, red=stale,
    // amber=no worker seen). Briefly flashes "All embedded" on completion
    // before going silent.
    let _lastBusy = false;
    let _flashUntil = 0;
    const HEARTBEAT_FRESH_S = 30;
    const pollQueue = async () => {
      try {
        const s = await fetchJson("GET", "/api/jobs/status");
        const j = s.jobs || {};
        const pending = j.pending || 0;
        const inflight = j.in_flight || 0;
        const failed = j.failed || 0;
        const completed = j.completed || 0;
        const total = pending + inflight + failed + completed;
        const busy = pending > 0 || inflight > 0;
        const link = $("#sidebar-queue");
        const text = $("#sidebar-queue-text");
        const dot = $("#sidebar-queue-dot");
        if (!link) return;

        // Worker heartbeat freshness — colors the dot when there's work
        // happening so users can tell "worker idle" from "worker down".
        let dotColor = "var(--mdb-leaf)";
        if (s.worker && s.worker.last_heartbeat) {
          const ageS = (Date.now() - Date.parse(s.worker.last_heartbeat)) / 1000;
          dotColor = ageS < HEARTBEAT_FRESH_S ? "var(--mdb-leaf)" : "var(--mdb-bad)";
        } else if (busy) {
          // Jobs queued but no worker has ever heartbeat — worker is down.
          dotColor = "var(--mdb-bad)";
        }
        if (failed) dotColor = "var(--mdb-bad)";

        // Flash "All embedded" for 5s when transitioning busy → idle.
        if (_lastBusy && !busy) _flashUntil = Date.now() + 5000;
        _lastBusy = busy;

        if (busy || failed) {
          link.style.display = "block";
          const parts = [];
          if (total > 0) {
            const done = completed;
            const pct = Math.floor((done / total) * 100);
            parts.push(`${done.toLocaleString()} / ${total.toLocaleString()} (${pct}%)`);
          }
          if (inflight) parts.push(`${inflight} running`);
          if (failed) parts.push(`${failed} failed`);
          text.textContent = parts.join(" · ");
          dot.style.background = dotColor;
        } else if (Date.now() < _flashUntil && total > 0) {
          link.style.display = "block";
          text.textContent = `✓ All embedded (${completed.toLocaleString()})`;
          dot.style.background = "var(--mdb-leaf)";
        } else {
          link.style.display = "none";
        }
      } catch { /* don't break the UI over a poll error */ }
    };
    pollQueue();
    setInterval(pollQueue, 5000);
    window.addEventListener("hashchange", route);
    route();

    // Mobile sidebar toggle. Click outside (the overlay) also closes it.
    const toggle = $("#sidebar-toggle");
    if (toggle) {
      toggle.onclick = () => document.body.classList.toggle("sidebar-open");
    }
    document.addEventListener("click", (ev) => {
      if (!document.body.classList.contains("sidebar-open")) return;
      const sb = $("#app-sidebar");
      if (sb && !sb.contains(ev.target) && ev.target !== toggle && !toggle.contains(ev.target)) {
        document.body.classList.remove("sidebar-open");
      }
    });
  })();
})();
