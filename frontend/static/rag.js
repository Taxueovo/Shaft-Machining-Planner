/**
 * ShaftPlanner RAG Management — frontend logic.
 *
 * Loaded only on /rag page. Uses the same fetch() pattern as the rest of
 * the project. All RAG API calls go through the frontend proxy (/api/rag/...)
 * which forwards to the backend (/api/v1/rag/...).
 *
 * If RAG is unavailable (chromadb not installed / embedding not configured),
 * the page shows a clear message and hides all action panels.
 */
(function () {
  "use strict";

  /* ─── DOM refs ─── */
  const ragAvailable = getEl("rag-unavailable");
  const ragContent = getEl("rag-content");
  const ragIndicator = getEl("rag-indicator");
  const specCount = getEl("spec-count");
  const caseCount = getEl("case-count");
  const specFiles = getEl("spec-files");
  const caseFiles = getEl("case-files");
  const specFileList = getEl("spec-file-list");
  const caseFileList = getEl("case-file-list");
  const searchInput = getEl("search-input");
  const searchResults = getEl("search-results");
  const chunksContent = getEl("chunks-content");

  let checking = false;

  /* ─── Init ─── */
  (async function init() {
    // Shutdown button (same pattern as all other pages)
    const btn = getEl("shutdown-btn");
    if (btn) {
      btn.addEventListener("click", async () => {
        if (!confirm("Confirm shutdown of ShaftPlanner - PE Agent system?")) return;
        btn.disabled = true;
        btn.textContent = "Shutting down...";
        try { await fetch("/api/shutdown", { method: "POST" }); } catch (_) {}
        setTimeout(() => {
          document.body.innerHTML =
            '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;color:#475467">' +
            '<div style="text-align:center"><h1>System Shutdown</h1><p>This page can be closed.</p></div></div>';
        }, 800);
      });
    }

    // Check RAG availability and load status
    await loadStatus();
  })();

  /* ─── Core API ─── */

  async function api(path, opts) {
    const res = await fetch("/api/rag" + path, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "API error");
    return data;
  }

  async function loadStatus() {
    try {
      const data = await api("/status", {});
      if (!data.available) {
        showUnavailable();
        return;
      }
      showAvailable();

      // Update counts
      const sc = data.specs.document_count;
      const cc = data.cases.document_count;
      setText(specCount, sc);
      setText(caseCount, cc);
      if (sc > 0) specCount.classList.remove("zero"); else specCount.classList.add("zero");
      if (cc > 0) caseCount.classList.remove("zero"); else caseCount.classList.add("zero");

      setText(specFiles, data.specs.source_files + " source files");
      setText(caseFiles, data.cases.source_files + " source files");

      // File lists
      renderFileList(specFileList, data.specs.files || []);
      renderFileList(caseFileList, data.cases.files || []);

      // Embedding info
      if (ragIndicator) {
        ragIndicator.textContent = "RAG Ready (" + data.embedding_model + ")";
        ragIndicator.className = "service ok";
      }
    } catch (e) {
      console.error("RAG status error:", e);
      showUnavailable();
    }
  }

  /* ─── Build ─── */

  window.buildIndex = async function (channel) {
    if (!confirm("Build " + channel + " index? Existing data will be replaced.")) return;

    const btnId = channel === "all" ? "btn-build-all"
      : channel === "specs" ? "btn-build-specs" : "btn-build-cases";
    const btn = getEl(btnId);

    const orig = btn.textContent;
    btn.textContent = "Building...";
    btn.disabled = true;

    try {
      const data = await api("/build?channel=" + channel, { method: "POST" });
      toast("Indexed " + data.chunks + " chunks in " + data.elapsed_s + "s", "success");
      await loadStatus();
      await loadChunks("all");
    } catch (e) {
      toast("Build failed: " + e.message, "error");
    } finally {
      btn.textContent = orig;
      btn.disabled = false;
    }
  };

  /* ─── Clear ─── */

  window.clearIndex = async function (channel) {
    if (!confirm("Clear " + channel + " index? This cannot be undone!")) return;
    try {
      await api("/clear?channel=" + channel, { method: "DELETE" });
      toast("Index cleared: " + channel, "info");
      await loadStatus();
      chunksContent.innerHTML = '<p class="empty">Index cleared.</p>';
    } catch (e) {
      toast("Clear failed: " + e.message, "error");
    }
  };

  /* ─── Search ─── */

  window.doSearch = async function () {
    const q = searchInput.value.trim();
    if (!q) return;

    searchResults.innerHTML = '<p class="muted">Searching...</p>';
    try {
      const data = await api("/search?q=" + encodeURIComponent(q) + "&top_k=5", {});
      if (!data.results || data.results.length === 0) {
        searchResults.innerHTML = '<p class="empty">No results found. Try different keywords.</p>';
        return;
      }

      searchResults.innerHTML = data.results.map((r, i) => {
        const channelIcon = r.channel === "specs" ? "📖" : "📋";
        const channelName = r.channel === "specs" ? "Specs" : "Cases";
        let metaLine = "";
        if (r.channel === "specs" && r.metadata && r.metadata.hierarchy_path) {
          metaLine = esc(r.metadata.hierarchy_path);
        } else if (r.channel === "cases" && r.metadata) {
          metaLine = esc((r.metadata.part_name || "") + " (" + (r.metadata.case_id || "") + ")");
        }
        return (
          '<div class="result-item">' +
          '<div class="r-header">' +
          '<span><strong>' + (i + 1) + '.</strong> ' + channelIcon + ' ' + channelName + '</span>' +
          '<span class="badge neutral">Score: ' + r.score.toFixed(3) + '</span>' +
          '</div>' +
          (metaLine ? '<div class="r-meta">' + metaLine + '</div>' : '') +
          '<div class="r-content">' + esc(r.content_preview) + '</div>' +
          '</div>'
        );
      }).join("");

      toast(data.total + " results found (" + data.spec_count + " specs, " + data.case_count + " cases)", "info");
    } catch (e) {
      searchResults.innerHTML = '<div class="alert danger">Search error: ' + esc(e.message) + '</div>';
    }
  };

  /* ─── Chunks ─── */

  window.loadChunks = async function (channel) {
    chunksContent.innerHTML = '<p class="muted">Loading...</p>';
    try {
      const data = await api("/chunks?channel=" + channel + "&limit=15", {});
      let html = "";
      for (const [ch, info] of Object.entries(data.channels)) {
        if (!info || info.count === 0) {
          html += '<p class="empty">No chunks indexed in ' + ch + '.</p>';
          continue;
        }
        html += '<p class="muted" style="margin-bottom:8px"><strong>' +
                (ch === "specs" ? "📖 Specs" : "📋 Cases") +
                ' — ' + info.count + ' chunks</strong></p>';
        for (const item of (info.items || [])) {
          let meta = "";
          if (ch === "specs" && item.hierarchy_path) {
            meta = '<span style="color:var(--primary)">' + esc(item.hierarchy_path) + '</span>';
          } else if (ch === "cases") {
            meta = '<span style="color:var(--primary)">' +
                   esc((item.part_name || "?") + " / " + (item.material || "?")) + '</span>' +
                   ' <span class="muted">[' + esc(item.case_id || "") + ']</span>';
          }
          html += '<details class="chunk-item">' +
                  '<summary>' + meta +
                  (item.chunk_id ? ' <span class="muted">[' + esc(item.chunk_id) + ']</span>' : '') +
                  '</summary>' +
                  '<div class="ch-content">' + esc(item.content || item.content_preview || "") + '</div>' +
                  '</details>';
        }
      }
      chunksContent.innerHTML = html || '<p class="empty">No chunks indexed.</p>';
    } catch (e) {
      chunksContent.innerHTML = '<div class="alert danger">Error: ' + esc(e.message) + '</div>';
    }
  };

  /* ─── UI Helpers ─── */

  function showAvailable() {
    ragAvailable.classList.add("hidden");
    ragContent.classList.remove("hidden");
  }

  function showUnavailable() {
    ragAvailable.classList.remove("hidden");
    ragContent.classList.add("hidden");
    if (ragIndicator) {
      ragIndicator.textContent = "RAG Unavailable";
      ragIndicator.className = "service bad";
    }
  }

  function renderFileList(el, files) {
    if (!el) return;
    if (files.length === 0) {
      el.innerHTML = '<li class="muted">(empty — add files then build)</li>';
    } else {
      el.innerHTML = files.map(f =>
        '<li>📄 ' + esc(f.name) + ' <span class="muted">(' + f.size_kb + ' KB)</span></li>'
      ).join("");
    }
  }

  function toast(msg, type) {
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => { el.remove(); }, 3000);
  }

  function setText(el, val) { if (el) el.textContent = val; }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function getEl(id) { return document.getElementById(id); }
})();
