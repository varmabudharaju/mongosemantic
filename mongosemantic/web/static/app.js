// mongosemantic web client. Vanilla ES2020+. No build step.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  let CONTENT = {};
  const PAGES = ["connection", "collections", "inspect", "apply", "indexing", "search", "query", "dashboard", "visualize", "mcp"];

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

  const route = () => {
    const hash = (location.hash || "#/connection").replace(/^#\//, "").split("/");
    const [page, ...args] = hash;
    if (!PAGES.includes(page)) { location.hash = "#/connection"; return; }
    showPage(page);
    handlers[page] && handlers[page](args.map(decodeURIComponent));
  };

  const handlers = {
    connection() { },

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
          <th></th>
        </tr></thead>`;
        const rows = data.collections.map(c => `<tr>
          <td>${escapeHtml(c.name)}</td>
          <td>${c.status === "configured"
            ? escapeHtml(CONTENT.collections.status_configured.replace("{n}", c.fields_count))
            : escapeHtml(CONTENT.collections.status_not_configured)}</td>
          <td><a href="#/inspect/${encodeURIComponent(c.name)}">${escapeHtml(CONTENT.collections.row_action)}</a></td>
        </tr>`).join("");
        tbl.innerHTML = head + "<tbody>" + rows + "</tbody>";
      } catch (e) { toast(e.message); }
    },

    inspect: async ([name]) => {
      if (!name) return;
      $("#inspect-title").textContent = CONTENT.inspect.title.replace("{collection}", name);
      $("#inspect-apply-link").href = `#/apply/${encodeURIComponent(name)}`;
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
      const f = $("#form-apply");
      const c = CONTENT.apply;
      f.innerHTML = `
        <fieldset>
          <legend>${escapeHtml(c.section_fields)}</legend>
          <input id="apply-fields" placeholder="comma-separated paths, e.g. body, title">
        </fieldset>
        <fieldset>
          <legend>${escapeHtml(c.section_mode)}</legend>
          <label><input type="radio" name="mode" value="shadow" checked> ${escapeHtml(c.mode_shadow)}</label>
          <br>
          <label><input type="radio" name="mode" value="inline"> ${escapeHtml(c.mode_inline)}</label>
        </fieldset>
        <fieldset>
          <legend>${escapeHtml(c.section_chunking)}</legend>
          <label><input id="apply-chunked" type="checkbox"> ${escapeHtml(c.chunking_toggle)}</label>
          <small>${escapeHtml(c.mode_chunk_notice)}</small>
        </fieldset>
        <fieldset>
          <legend>${escapeHtml(c.section_model)}</legend>
          <select id="apply-model">
            <option value="local-fast">${escapeHtml(c.model_local_fast)}</option>
            <option value="local-better">${escapeHtml(c.model_local_better)}</option>
            <option value="openai-small">${escapeHtml(c.model_openai_small)}</option>
            <option value="openai-large">${escapeHtml(c.model_openai_large)}</option>
            <option value="ollama-nomic">${escapeHtml(c.model_ollama_nomic)}</option>
          </select>
        </fieldset>
        <button type="submit">${escapeHtml(c.cta_apply)}</button>
      `;
      f.onsubmit = async ev => {
        ev.preventDefault();
        const fields = $("#apply-fields").value.split(",").map(s => s.trim()).filter(Boolean);
        const mode = (f.querySelector('input[name="mode"]:checked') || {}).value || "shadow";
        const chunked = $("#apply-chunked").checked;
        const model = $("#apply-model").value;
        try {
          await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/apply`,
            { fields, mode, chunked, model });
          toast(CONTENT.global.toast_config_updated);
          location.hash = `#/indexing/${encodeURIComponent(name)}`;
        } catch (e) { toast(e.message); }
      };
    },

    indexing: async ([name]) => {
      if (!name) return;
      $("#indexing-title").textContent = CONTENT.indexing.title.replace("{collection}", name);
      try {
        const r = await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/index`);
        const bar = $("#indexing-progress");
        bar.max = r.total || r.enqueued || 1;
        bar.value = r.enqueued;
        $("#indexing-metric").textContent = CONTENT.indexing.metric_progress
          .replace("{processed}", r.enqueued).replace("{total}", r.total);
        toast(CONTENT.indexing.toast_complete.replace("{n}", r.enqueued));
      } catch (e) { toast(e.message); }
    },

    search: async () => {
      const c = CONTENT.search;
      const empty = $("#search-empty");
      const results = $("#search-results");
      empty.textContent = c.empty_no_query;
      results.innerHTML = "";
      const sel = $("#search-collection");
      try {
        const cols = await fetchJson("GET", "/api/collections");
        sel.innerHTML = `<option value="">${escapeHtml(c.selector_all)}</option>` +
          cols.collections.filter(x => x.status === "configured")
            .map(x => `<option value="${escapeHtml(x.name)}">${escapeHtml(x.name)}</option>`).join("");
      } catch { /* leave empty */ }
      const input = $("#search-q");
      input.placeholder = c.placeholder;
      let timer;
      input.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = input.value.trim();
          if (!q) { results.innerHTML = ""; empty.textContent = c.empty_no_query; return; }
          const params = new URLSearchParams({ q });
          if (sel.value) params.set("collection", sel.value);
          try {
            const r = await fetchJson("GET", "/api/search?" + params.toString());
            if (!r.rows.length) { empty.textContent = c.empty_no_results; results.innerHTML = ""; return; }
            empty.textContent = "";
            results.innerHTML = r.rows.map(row => `<li>
              <strong>${(row.score || 0).toFixed(3)}</strong>
              <span>${escapeHtml(row.source_collection)}</span>
              <span>${escapeHtml(row.field_path)}</span>
              <p>${escapeHtml((row.chunk_text || "").slice(0, 300))}</p>
            </li>`).join("");
          } catch (e) { toast(e.message); }
        }, 300);
      };
    },

    query: async () => {
      const ta = $("#query-pipeline");
      const out = $("#query-results");
      $("#query-run").onclick = async () => {
        let pipeline;
        try { pipeline = JSON.parse(ta.value); }
        catch { toast(CONTENT.aggregation.error_rejected.replace("{reason}", "invalid JSON")); return; }
        const collection = $("#query-collection").value.trim();
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
      try {
        const d = await fetchJson("GET", "/api/dashboard");
        $("#dashboard-cards").innerHTML = `
          <div><strong>${d.configured_count}</strong><br>${escapeHtml(CONTENT.dashboard.card_collections)}</div>
          <div><strong>${d.total_embeddings}</strong><br>${escapeHtml(CONTENT.dashboard.card_total_embeddings)}</div>
          <div><strong>${d.jobs.pending || 0}</strong><br>${escapeHtml(CONTENT.dashboard.card_pending)}</div>
          <div><strong>${d.jobs.failed || 0}</strong><br>${escapeHtml(CONTENT.dashboard.card_failed)}</div>
          <div><strong>${d.topology}</strong><br>topology</div>
        `;
      } catch (e) { toast(e.message); }
      $("#dashboard-retry").onclick = async () => {
        try {
          const r = await fetchJson("POST", "/api/jobs/retry");
          toast(`reset ${r.reset} failed job(s)`);
          handlers.dashboard();
        } catch (e) { toast(e.message); }
      };
    },

    visualize() { },
    mcp() { },
  };

  // Bootstrap
  (async () => {
    try {
      CONTENT = await fetchJson("GET", "/api/content");
      hydrateContent();
      $$("#app-nav a").forEach(a => {
        const k = "nav_" + a.dataset.page;
        if (CONTENT.global && CONTENT.global[k]) a.textContent = CONTENT.global[k];
      });
    } catch (e) {
      console.error("content load failed", e);
    }
    window.addEventListener("hashchange", route);
    route();

    const cf = $("#form-connection");
    if (cf) cf.addEventListener("submit", async ev => {
      ev.preventDefault();
      const uri = $("#conn-uri").value.trim();
      const database = $("#conn-db").value.trim();
      $("#conn-state").textContent = CONTENT.connection.state_connecting;
      try {
        const r = await fetchJson("POST", "/api/connect", { uri, database });
        const stateKey = `state_${r.topology}`;
        $("#conn-state").textContent = CONTENT.connection[stateKey] || JSON.stringify(r);
      } catch (e) {
        $("#conn-state").textContent = e.message;
      }
    });
  })();
})();
