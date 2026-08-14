(() => {
  // Shutdown button
  const shutdownBtn = document.getElementById("shutdown-btn");
  if (shutdownBtn) {
    shutdownBtn.addEventListener("click", async () => {
      if (!confirm("Confirm shutdown of ShaftPlanner system? Both frontend and backend will stop.")) return;
      shutdownBtn.disabled = true;
      shutdownBtn.textContent = "Shutting down...";
      try {
        await fetch("/api/shutdown", { method: "POST" });
      } catch {}
      setTimeout(() => {
        document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;color:#475467"><div style="text-align:center"><h1 style="margin:0 0 12px">System Shutdown</h1><p>This page can be closed.</p></div></div>';
      }, 800);
    });
  }

  const jobId = window.SHAFTPLANNER_JOB_ID;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "")
    .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
    .replaceAll('"',"&quot;").replaceAll("'","&#039;");
  let rendered = false, timer = null;
  // Process route customization state: customRoute takes priority over originalRoute; operation_no is the stable resource key
  let customRoute = null, originalRoute = [], routeResourceMap = {}, routeScopeNote = "";
  let editOps = [], dragIndex = -1;

  // ── CAD session: fetch the cad_agent 3D render images via the URL ?cad=token ──
  let __cadImages = null;
  let __cadSourceFile = null;
  (async () => {
    const token = new URLSearchParams(window.location.search).get("cad");
    if (!token) return;
    try {
      const resp = await fetch(`/api/cad-session/${token}`);
      if (!resp.ok) return;
      const data = await resp.json();
      __cadImages = data.images || null;
      __cadSourceFile = data.source_file || null;
      if (data.source_file) {
        // Clean up the server-side cache after consumption
        try { await fetch(`/api/cad-session/${token}`, { method: "DELETE" }); } catch {}
      }
      if (rendered && __cadImages) {
        const container = $("shaft-3d-container");
        if (container) renderCadImages(container, __cadImages, __cadSourceFile);
      }
    } catch {}
  })();

  // Render the cad_agent multi-view 3D render images (replacing the Three.js procedural model)
  function renderCadImages(container, images, sourceFile) {
    if (!container) return;
    container.innerHTML = "";
    container.style.height = "auto";
    const views = { front: "Front", top: "Top", right: "Right", isometric: "Isometric" };
    const grid = document.createElement("div");
    grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px";
    let any = false;
    Object.entries(images || {}).forEach(([key, img]) => {
      if (!img || !img.success || !img.base64) return;
      any = true;
      const fig = document.createElement("figure");
      fig.style.cssText = "margin:0;text-align:center";
      const el = document.createElement("img");
      el.src = img.base64.startsWith("data:") ? img.base64 : "data:image/jpeg;base64," + img.base64;
      el.alt = key;
      el.style.cssText = "width:100%;border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc";
      const cap = document.createElement("figcaption");
      cap.textContent = views[key] || key;
      cap.style.cssText = "font-size:11px;color:#64748b;margin-top:4px";
      fig.appendChild(el); fig.appendChild(cap);
      grid.appendChild(fig);
    });
    if (any) {
      container.appendChild(grid);
      if (sourceFile) {
        const note = document.createElement("div");
        note.style.cssText = "font-size:11px;color:#94a3b8;margin-top:6px";
        note.textContent = "3D render by cad_agent · source: " + sourceFile;
        container.appendChild(note);
      }
    }
  }

  function setBadge(status) {
    const map = {
      queued:["Queued","neutral"], running:["Running","neutral"],
      waiting_user_choice:["Waiting","warning"], completed:["Completed","success"],
      resource_mismatch:["Mismatch","danger"], failed:["Failed","danger"]
    };
    const [label, cls] = map[status] || [status,"neutral"];
    $("status-badge").textContent = label;
    $("status-badge").className = `badge ${cls}`;
  }

  function showError(message) {
    $("error-message").textContent = message;
    $("error-panel").classList.remove("hidden");
  }

  function showChoices(items) {
    $("choice-list").innerHTML = items.map(item => `
      <div class="feature-card" data-id="${esc(item.feature_id)}">
        <div class="feature-title"><h3>${esc(item.feature_id)} · ${esc(item.feature_name)}</h3><span class="badge warning">High Precision</span></div>
        <div class="data-row"><span>Global Position</span><strong>${esc(item.global_position_mm)} mm</strong></div>
        <div class="data-row"><span>Tolerance</span><strong>${esc(item.tolerance_lower_mm)} / ${esc(item.tolerance_upper_mm)} mm</strong></div>
        <div class="data-row"><span>Roughness</span><strong>${item.roughness_ra ? `Ra ${esc(item.roughness_ra)}` : "Not filled"}</strong></div>
        ${item.options.map(option => `
          <div class="choice"><label>
            <input type="radio" name="choice-${esc(item.feature_id)}" value="${esc(option.value)}" ${option.value === item.recommended ? "checked" : ""}>
            <span><strong>${esc(option.label)}</strong><br><span class="muted">${esc(option.description)}</span></span>
          </label></div>`).join("")}
      </div>`).join("");
    $("choice-panel").classList.remove("hidden");
  }

  $("choice-form").addEventListener("submit", async event => {
    event.preventDefault();
    const cards = [...$("choice-list").querySelectorAll(".feature-card")];
    const unchecked = cards.find(card => !card.querySelector("input[type=radio]:checked"));
    if (unchecked) {
      showError(`Please select timing for feature ${unchecked.dataset.id}.`);
      return;
    }
    const choices = cards.map(card => ({
      feature_id: card.dataset.id,
      processing_timing: card.querySelector("input[type=radio]:checked").value
    }));
    const response = await fetch(`/api/jobs/${jobId}/choices`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({choices})
    });
    const data = await response.json();
    if (!response.ok) { showError(JSON.stringify(data.detail || data)); return; }
    $("choice-panel").classList.add("hidden");
    schedule(300);
  });

  const panel = (title, content, badge="") => `
    <section class="panel"><div class="heading"><div><h2>${esc(title)}</h2></div>${badge}</div>${content}</section>`;

  function machineTable(items) {
    if (!items.length) return `<div class="empty">No matching machines.</div>`;
    return `<div class="table-wrap"><table>
      <thead><tr><th>Machine</th><th>Manufacturer</th><th>Turning Length</th><th>Blank Dia</th><th>Chuck Dia</th><th>Clamping</th><th>Source</th></tr></thead>
      <tbody>${items.map(x => `<tr>
        <td>${esc(x.designation)}</td><td>${esc(x.manufacturer)}</td>
        <td>${esc(x.turning_length_mm)} mm</td>
        <td>${esc(x.max_turning_diameter_rod_mm ?? "-")}</td>
        <td>${esc(x.max_turning_diameter_chuck_mm ?? "-")}</td>
        <td>${esc((x.supported_loading_modes || []).join(" / "))}</td>
        <td>${esc(x.production_status_source)}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function renderResult(payload) {
    if (rendered) return;
    rendered = true;

    if (!payload.geometry && !payload.process_route && !payload.verification) {
      $("result-container").innerHTML = panel("Execution Result", `<div class="alert danger">${esc(payload.error || payload.message || "Result data is empty.")}</div>`);
      $("result-container").classList.remove("hidden");
      return;
    }

    if (payload.status === "resource_mismatch") {
      const cap = payload.capability || {};
      $("result-container").innerHTML = panel(
        "Resource Capability Check Failed",
        `<div class="alert danger">${esc(cap.machine?.message || payload.message)}</div>
         ${machineTable(cap.machine?.active_matches || [])}
         ${Object.entries(cap.tool_checks || {}).map(([name,x]) =>
           `<div class="data-row"><span>${esc(name)}</span><strong>${esc(x.conclusion)}</strong></div>`
         ).join("")}`,
        `<span class="badge danger">Terminated</span>`
      );
      $("result-container").classList.remove("hidden");
      return;
    }

    const geo = payload.geometry || {};
    const verify = payload.verification || {};
    const resources = payload.resource_selection || {};
    // Show the user-customized route first; operation_no stays as the stable resource key (resources follow operations after reordering)
    originalRoute = payload.process_route || [];
    if (Array.isArray(payload.custom_route) && payload.custom_route.length) customRoute = payload.custom_route;
    const route = customRoute && customRoute.length ? customRoute : originalRoute;
    routeScopeNote = resources.scope_note || "";
    const verifyClass = verify.conclusion === "pass" ? "success" :
      verify.conclusion === "conditional_pass" ? "warning" : "danger";

    const html = [];

    html.push(panel(
      "Verification Result",
      `<p>${esc(verify.message || "")}</p>
       ${(verify.checks || []).map(x => `<div class="data-row"><span>${esc(x.name)}</span><strong>${x.passed ? "✓ Passed" : "✕ Failed"}</strong></div>`).join("")}
       ${(verify.warnings || []).length ? `<div class="alert warning" style="margin-top:14px">${verify.warnings.map(esc).join("<br>")}</div>` : ""}`,
      `<span class="badge ${verifyClass}">${esc(verify.conclusion)}</span>`
    ));

    html.push(panel(
      "Shaft Geometry",
      `<div class="result-grid">
        <div>
          <div class="data-row"><span>Total Length</span><strong>${esc(geo.total_length_mm)} mm</strong></div>
          <div class="data-row"><span>Blank Diameter</span><strong>φ${esc(geo.blank_diameter_mm)} mm</strong></div>
          <div class="data-row"><span>Max Finished Dia</span><strong>φ${esc(geo.max_finished_diameter_mm)} mm</strong></div>
          <div style="margin-top:8px">${(geo.segments || []).map(s =>
            `<div class="data-row"><span>${esc(s.segment_id)}</span><strong>φ${esc(s.diameter_mm)} × ${esc(s.length_mm)} mm</strong></div>`
          ).join("")}</div>
        </div>
        <div id="shaft-3d-container" class="shaft-3d"></div>
      </div>`
    ));

    html.push(panel(
      "Conditional Features",
      (geo.features || []).length ? `<div class="table-wrap"><table>
        <thead><tr><th>ID</th><th>Type</th><th>Segment</th><th>Position</th><th>Precision</th></tr></thead>
        <tbody>${geo.features.map(f => `<tr>
          <td>${esc(f.feature_id)}</td><td>${esc(f.feature_type)}</td>
          <td>${esc(f.resolved_segment_id || "-")}</td><td>${esc(f.global_position_mm)} mm</td>
          <td>${f.high_precision ? '<span class="badge warning">High</span>' : '<span class="badge neutral">Normal</span>'}</td>
        </tr>`).join("")}</tbody></table></div>` : `<div class="empty">No conditional features.</div>`
    ));

    // Merge process route and resource data (shared by the read-only view and edit mode; keyed by the stable operation_no)
    const resourceMap = {};
    (resources.operation_resources || []).forEach(x => {
      resourceMap[x.operation_no] = x;
    });
    routeResourceMap = resourceMap;

    html.push(panel(
      "Process Route & Resources",
      `<div id="route-panel-inner">${routePanelInnerHtml(route)}</div>
       <div id="process-card-container"></div>`
    ));

    // Execution trace panel
    const trace = payload.execution_trace || payload.result?.execution_trace || [];
    if (trace.length) {
      html.push(panel(
        "Agent Execution Trace",
        `<div class="table-wrap"><table>
          <thead><tr><th>Node</th><th>Status</th><th>Duration</th><th>Input</th><th>Output</th><th>Tools</th></tr></thead>
          <tbody>${trace.map(t => `<tr>
            <td><strong>${esc(t.node)}</strong></td>
            <td><span class="badge ${t.status === 'success' ? 'success' : 'danger'}">${esc(t.status)}</span></td>
            <td>${t.duration_ms != null ? esc(t.duration_ms) + ' ms' : '-'}</td>
            <td class="muted">${esc((t.input_keys || []).join(', '))}</td>
            <td class="muted">${esc((t.output_keys || []).join(', '))}</td>
            <td>${(t.tool_calls || []).map(tc =>
              `<div style="margin:2px 0"><span class="tag">${esc(tc.tool)}</span> <span class="muted">${esc(tc.duration_ms)}ms</span> <span class="muted">${esc(tc.result_summary)}</span></div>`
            ).join("") || '-'}</td>
          </tr>`).join("")}</tbody></table></div>`,
        `<span class="badge neutral">${trace.length} steps</span>`
      ));
    }

    $("result-container").innerHTML = html.join("");
    $("result-container").classList.remove("hidden");

    // Render 3D shaft model — with a CAD import, show the cad_agent 3D render (replacing the procedural model)
    function doRender3D() {
      const container = $("shaft-3d-container");
      if (!container) return;
      const hasCadImages = __cadImages && Object.keys(__cadImages).some(k => __cadImages[k] && __cadImages[k].success);
      if (hasCadImages) {
        renderCadImages(container, __cadImages, __cadSourceFile);
        return;
      }
      try { renderShaft3D("shaft-3d-container", geo); } catch (e) { console.warn("3D render failed:", e); }
    }
    if (typeof renderShaft3D === "function") {
      doRender3D();
    } else {
      window.addEventListener("shaft3d-ready", doRender3D, { once: true });
    }

    bindRouteButtons();
  }

  async function generateProcessCard(btn) {
    const container = document.getElementById("process-card-container");
    if (!container) return;
    btn.disabled = true;
    btn.textContent = "Generating...";
    container.innerHTML = '<div class="muted" style="padding:12px">Generating process card, please wait...</div>';
    try {
      const resp = await fetch(`/api/jobs/${jobId}/process-card/export`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(JSON.stringify(data.detail || data));
      container.innerHTML = `<div class="alert success">Process card exported: <strong>${esc(data.file_path)}</strong></div>`;
    } catch (err) {
      container.innerHTML = `<div class="alert danger">Export failed: ${esc(err.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Generate Process Card";
    }
  }

  // ── Process route customization (before generating the process card: reorder / edit / add or delete operations) ──
  // Keep in sync with the backend models/process.py ProcessStage / MANDATORY_OPERATION_NAMES
  const ROUTE_STAGES = ["blank","datum","rough","semi_finish","finish_before_heat","feature_before_heat",
    "pre_heat_treatment","heat_treatment","datum_recovery","finish","feature_after_heat",
    "precision_finish","precision_feature","feature_before_inspection","deburr",
    "surface_treatment","inspection","packaging"];
  const MANDATORY_OPS = ["Blanking","Face Turning","Center Drilling","Rough Turning",
    "Semi-finish Turning","Finish Turning","Final Inspection"];

  const opDeviceHtml = res => {
    const machines = res.machine_recommendations || [];
    const tools = res.tool_recommendations || [];
    const blocks = [];
    if (machines.length) {
      blocks.push(`<div class="op-device"><span class="dev-label">Machine:</span> ${machines.map(m =>
        `${esc(m.designation)}<span class="muted"> (${esc(m.manufacturer)})</span>`
      ).join(" · ")}</div>`);
    }
    if (tools.length) {
      blocks.push(`<div class="op-device"><span class="dev-label">Tool:</span> ${tools.map(t =>
        esc(t.cutting_tool_grade)
      ).filter(Boolean).join(" · ")}</div>`);
    }
    return blocks.length ? `<div class="op-devices">${blocks.join("")}</div>` : "";
  };

  function opHtml(op, idx) {
    const res = routeResourceMap[op.operation_no] || {};
    return `<div class="operation">
      <div class="op-no">${esc(idx + 1)}</div>
      <div><div class="op-name">${esc(op.name)}</div></div>
      <div><div class="muted">${esc(op.description)}${res.note ? ` ${esc(res.note)}` : ""}</div>${opDeviceHtml(res)}</div>
    </div>`;
  }

  function routePanelInnerHtml(ops) {
    return `<div id="route-op-list">${ops.map(opHtml).join("")}</div>
      <div class="alert warning" style="margin-top:16px">${esc(routeScopeNote)}</div>
      <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
        <button id="customize-route-btn" class="button secondary">Customize Route</button>
        <button id="gen-process-card-btn" class="button primary">Generate Process Card</button>
      </div>`;
  }

  function bindRouteButtons() {
    const cardBtn = document.getElementById("gen-process-card-btn");
    if (cardBtn) cardBtn.addEventListener("click", () => generateProcessCard(cardBtn));
    const customBtn = document.getElementById("customize-route-btn");
    if (customBtn) customBtn.addEventListener("click", enterEditMode);
  }

  function rerenderRoutePanel() {
    const inner = document.getElementById("route-panel-inner");
    if (!inner) return;
    const route = customRoute && customRoute.length ? customRoute : originalRoute;
    inner.innerHTML = routePanelInnerHtml(route);
    bindRouteButtons();
  }

  function editRowHtml(op, idx) {
    const res = routeResourceMap[op.operation_no] || {};
    const machines = res.machine_recommendations || [];
    const tools = res.tool_recommendations || [];
    const resText = [...machines.map(m => m.designation), ...tools.map(t => t.cutting_tool_grade)]
      .filter(Boolean).join(", ") || "-";
    return `<tr class="route-edit-row" draggable="true">
      <td class="drag-handle" title="Drag to reorder">⠿</td>
      <td class="seq-no">${idx + 1}</td>
      <td><input class="edit-name" value="${esc(op.name)}" placeholder="Operation name" /></td>
      <td><select class="edit-stage">${ROUTE_STAGES.map(s =>
        `<option value="${esc(s)}"${s === op.stage ? " selected" : ""}>${esc(s)}</option>`
      ).join("")}</select></td>
      <td><input class="edit-desc" value="${esc(op.description || "")}" placeholder="Description" /></td>
      <td class="edit-res muted">${esc(resText)}</td>
      <td class="edit-actions">
        <button type="button" class="mini-btn" data-act="up" title="Move up">↑</button>
        <button type="button" class="mini-btn" data-act="down" title="Move down">↓</button>
        <button type="button" class="mini-btn danger" data-act="del" title="Delete">✕</button>
      </td>
    </tr>`;
  }

  function editorInnerHtml() {
    return `<table class="route-edit-table">
      <thead><tr><th></th><th>#</th><th>Operation</th><th>Stage</th><th>Description</th><th>Resources</th><th>Actions</th></tr></thead>
      <tbody>${editOps.map(editRowHtml).join("")}</tbody>
    </table>
    <div class="route-edit-toolbar">
      <button type="button" id="route-add-op" class="button ghost">+ Add Operation</button>
      <span class="muted" style="font-size:12px">Drag rows or use the arrows to reorder.</span>
    </div>
    <div class="route-edit-actions">
      <button type="button" id="route-save" class="button primary">Save Custom Route</button>
      <button type="button" id="route-reset" class="button ghost danger">Reset to Original</button>
      <button type="button" id="route-cancel" class="button ghost">Cancel</button>
    </div>`;
  }

  function syncEditOpsFromDom() {
    const rows = document.querySelectorAll("#route-panel-inner .route-edit-row");
    editOps = Array.from(rows).map((tr, i) => {
      const base = editOps[i] || {};
      return {
        operation_no: base.operation_no,
        name: tr.querySelector(".edit-name").value,
        stage: tr.querySelector(".edit-stage").value,
        description: tr.querySelector(".edit-desc").value,
        process_category: base.process_category ?? null,
        feature_id: base.feature_id ?? null,
        conditional: !!base.conditional,
      };
    });
  }

  function rerenderEditor() {
    const inner = document.getElementById("route-panel-inner");
    if (!inner) return;
    inner.innerHTML = editorInnerHtml();
    bindEditorEvents();
  }

  function enterEditMode() {
    const inner = document.getElementById("route-panel-inner");
    if (!inner) return;
    editOps = (customRoute && customRoute.length ? customRoute : originalRoute).map(op => ({ ...op }));
    inner.innerHTML = editorInnerHtml();
    bindEditorEvents();
  }

  function bindEditorEvents() {
    const tbody = document.querySelector("#route-panel-inner .route-edit-table tbody");
    if (!tbody) return;

    // Row action buttons (↑ / ↓ / ✕) — event delegation
    tbody.addEventListener("click", event => {
      const btn = event.target.closest("button[data-act]");
      if (!btn) return;
      const tr = btn.closest("tr");
      const idx = Array.prototype.indexOf.call(tbody.children, tr);
      if (idx < 0) return;
      syncEditOpsFromDom();
      const act = btn.dataset.act;
      if (act === "up" && idx > 0) {
        [editOps[idx - 1], editOps[idx]] = [editOps[idx], editOps[idx - 1]];
        rerenderEditor();
      } else if (act === "down" && idx < editOps.length - 1) {
        [editOps[idx + 1], editOps[idx]] = [editOps[idx], editOps[idx + 1]];
        rerenderEditor();
      } else if (act === "del") {
        const target = editOps[idx];
        if (MANDATORY_OPS.includes(target.name) &&
            !confirm(`"${target.name}" is a mandatory operation. Delete it anyway?`)) return;
        editOps.splice(idx, 1);
        rerenderEditor();
      }
    });

    const addBtn = document.getElementById("route-add-op");
    if (addBtn) addBtn.addEventListener("click", () => {
      syncEditOpsFromDom();
      const maxNo = editOps.reduce((m, o) => Math.max(m, Number(o.operation_no) || 0), 0);
      editOps.push({
        operation_no: maxNo + 1, name: "", stage: "rough",
        description: "", process_category: null, feature_id: null, conditional: false,
      });
      rerenderEditor();
    });

    const saveBtn = document.getElementById("route-save");
    if (saveBtn) saveBtn.addEventListener("click", saveCustomRoute);

    const resetBtn = document.getElementById("route-reset");
    if (resetBtn) resetBtn.addEventListener("click", resetCustomRoute);

    const cancelBtn = document.getElementById("route-cancel");
    if (cancelBtn) cancelBtn.addEventListener("click", rerenderRoutePanel);

    // Drag-and-drop reordering (reorder on drop, keeping the DOM and editOps order in sync)
    const rows = tbody.querySelectorAll(".route-edit-row");
    rows.forEach(row => {
      row.addEventListener("dragstart", event => {
        if (event.target.closest("input,select,button")) { event.preventDefault(); return; }
        dragIndex = Array.prototype.indexOf.call(tbody.children, row);
        row.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
      });
      row.addEventListener("dragend", () => {
        row.classList.remove("dragging");
        tbody.querySelectorAll(".route-edit-row").forEach(r => r.classList.remove("drag-over"));
        dragIndex = -1;
      });
      row.addEventListener("dragover", event => {
        event.preventDefault();
        const target = event.target.closest(".route-edit-row");
        if (!target || target === row) return;
        tbody.querySelectorAll(".route-edit-row").forEach(r => r.classList.remove("drag-over"));
        target.classList.add("drag-over");
      });
      row.addEventListener("drop", event => {
        event.preventDefault();
        const target = event.target.closest(".route-edit-row");
        if (!target || dragIndex < 0) return;
        const toIndex = Array.prototype.indexOf.call(tbody.children, target);
        syncEditOpsFromDom();                 // DOM order == editOps order (not reordered live)
        const fromIndex = dragIndex;
        const [moved] = editOps.splice(fromIndex, 1);
        editOps.splice(toIndex > fromIndex ? toIndex - 1 : toIndex, 0, moved);
        dragIndex = -1;
        rerenderEditor();
      });
    });
  }

  async function saveCustomRoute() {
    syncEditOpsFromDom();
    if (!editOps.length) { showError("Route cannot be empty."); return; }
    const missing = editOps.find(op => !op.name || !op.name.trim());
    if (missing) { showError("Every operation needs a name."); return; }
    const numbers = editOps.map(o => o.operation_no);
    if (new Set(numbers).size !== numbers.length) { showError("Operation numbers must be unique."); return; }
    const saveBtn = document.getElementById("route-save");
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving..."; }
    try {
      const resp = await fetch(`/api/jobs/${jobId}/process-route/customize`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ operations: editOps })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(JSON.stringify(data.detail || data));
      customRoute = data.operations || editOps;
      rerenderRoutePanel();
    } catch (err) {
      showError(`Save custom route failed: ${err.message}`);
    } finally {
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Save Custom Route"; }
    }
  }

  async function resetCustomRoute() {
    if (!confirm("Reset the process route to the original generated route? Your custom changes will be discarded.")) return;
    const resetBtn = document.getElementById("route-reset");
    if (resetBtn) { resetBtn.disabled = true; resetBtn.textContent = "Resetting..."; }
    try {
      const resp = await fetch(`/api/jobs/${jobId}/process-route/customization`, { method: "DELETE" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(JSON.stringify(data.detail || data));
      customRoute = null;
      rerenderRoutePanel();
    } catch (err) {
      showError(`Reset route failed: ${err.message}`);
    } finally {
      if (resetBtn) { resetBtn.disabled = false; resetBtn.textContent = "Reset to Original"; }
    }
  }

  async function poll() {
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      const data = await response.json();
      if (!response.ok) throw new Error(JSON.stringify(data.detail || data));
      setBadge(data.status);
      $("progress-bar").style.width = `${data.progress}%`;
      $("progress-value").textContent = `${data.progress}%`;
      $("current-step").textContent = data.current_step;
      $("status-message").textContent = data.message;
      if (data.error) showError(data.error);

      if (data.status === "waiting_user_choice") return showChoices(data.pending_choices || []);
      if (["completed","resource_mismatch","failed"].includes(data.status) && data.result_ready) {
        const r = await fetch(`/api/jobs/${jobId}/result`);
        const output = await r.json();
        if (!r.ok) throw new Error(JSON.stringify(output.detail || output));
        try {
          return renderResult(output);
        } catch (renderErr) {
          console.error("[ShaftPlanner] renderResult error:", renderErr);
          showError(`Result rendering failed: ${renderErr.message}`);
        }
      }
      schedule(800);
    } catch (error) {
      showError(`Status fetch failed: ${error.message}`);
      schedule(1800);
    }
  }

  function schedule(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
  poll();
})();
