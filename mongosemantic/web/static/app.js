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
          const actions = isConf
            ? `<a href="#/inspect/${encodeURIComponent(c.name)}">Inspect</a>
               &nbsp;·&nbsp;
               <a href="#" data-migrate="${escapeHtml(c.name)}" data-mode="${escapeHtml(c.mode || "")}">Migrate model</a>`
            : `<a href="#/inspect/${encodeURIComponent(c.name)}">${escapeHtml(CONTENT.collections.row_action)}</a>`;
          return `<tr>
            <td><strong>${escapeHtml(c.name)}</strong></td>
            <td>${pill}</td>
            <td>${model}</td>
            <td style="text-align:right">${actions}</td>
          </tr>`;
        }).join("");
        tbl.innerHTML = head + "<tbody>" + rows + "</tbody>";
        // Wire the per-row migrate action.
        $$("a[data-migrate]", tbl).forEach(a => {
          a.onclick = (ev) => {
            ev.preventDefault();
            openMigrateModal(a.dataset.migrate, a.dataset.mode);
          };
        });
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
      const notice = $("#search-notice");
      empty.textContent = c.empty_no_query;
      results.innerHTML = "";
      notice.textContent = "";
      const sel = $("#search-collection");
      const hybridBox = $("#search-hybrid");
      try {
        const cols = await fetchJson("GET", "/api/collections");
        sel.innerHTML = `<option value="">${escapeHtml(c.selector_all)}</option>` +
          cols.collections.filter(x => x.status === "configured")
            .map(x => `<option value="${escapeHtml(x.name)}">${escapeHtml(x.name)}</option>`).join("");
      } catch { /* leave empty */ }
      const input = $("#search-q");
      input.placeholder = c.placeholder;
      let timer;
      const run = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = input.value.trim();
          if (!q) {
            results.innerHTML = ""; notice.textContent = "";
            empty.textContent = c.empty_no_query; return;
          }
          const params = new URLSearchParams({ q });
          if (sel.value) params.set("collection", sel.value);
          if (hybridBox.checked) params.set("hybrid", "true");
          try {
            const r = await fetchJson("GET", "/api/search?" + params.toString());
            notice.textContent = r.notice || "";
            if (!r.rows.length) { empty.textContent = c.empty_no_results; results.innerHTML = ""; return; }
            empty.textContent = "";
            results.innerHTML = r.rows.map(row => `<li>
              <strong>${(row.score || 0).toFixed(3)}</strong>
              <div>
                <div class="meta">
                  <span class="coll">${escapeHtml(row.source_collection)}</span>
                  <span>·</span>
                  <span>${escapeHtml(row.field_path)}</span>
                </div>
                <p>${escapeHtml((row.chunk_text || "").slice(0, 300))}</p>
              </div>
            </li>`).join("");
          } catch (e) { toast(e.message); }
        }, 300);
      };
      input.oninput = run;
      sel.onchange = run;
      hybridBox.onchange = run;
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
      } catch (e) { toast(e.message); }
      $("#dashboard-retry").onclick = async () => {
        try {
          const r = await fetchJson("POST", "/api/jobs/retry");
          toast(`reset ${r.reset} failed job(s)`);
          handlers.dashboard();
        } catch (e) { toast(e.message); }
      };
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
