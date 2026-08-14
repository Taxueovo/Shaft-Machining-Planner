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

  // Load material list
  const materialSelect = document.getElementById("material");
  const materialDesc = document.getElementById("material-desc");
  let materialsData = [];

  async function loadMaterials() {
    try {
      const response = await fetch("/api/materials");
      const data = await response.json();
      materialsData = data.materials || [];

      // Group by category
      const categories = {
        "P": { label: "Carbon / Alloy Steel", materials: [] },
        "M": { label: "Stainless Steel", materials: [] },
        "H": { label: "Bearing Steel", materials: [] },
        "N": { label: "Aluminum / Copper Alloy", materials: [] },
        "S": { label: "Superalloy / Titanium", materials: [] },
      };

      materialsData.forEach(m => {
        if (categories[m.category]) {
          categories[m.category].materials.push(m);
        }
      });

      // Generate dropdown options
      let html = '<option value="">Select material</option>';
      Object.entries(categories).forEach(([key, cat]) => {
        if (cat.materials.length > 0) {
          html += `<optgroup label="${cat.label}">`;
          cat.materials.forEach(m => {
            html += `<option value="${m.value}" data-desc="${m.description}">${m.label}</option>`;
          });
          html += '</optgroup>';
        }
      });

      materialSelect.innerHTML = html;

      // Default to 45 steel
      materialSelect.value = "45";
      updateMaterialDesc();
    } catch (error) {
      materialSelect.innerHTML = '<option value="45">45 Steel</option><option value="40Cr">40Cr</option><option value="304">304 Stainless</option>';
      console.error("Failed to load materials:", error);
    }
  }

  function updateMaterialDesc() {
    const selected = materialSelect.options[materialSelect.selectedIndex];
    if (selected && selected.dataset.desc) {
      materialDesc.textContent = selected.dataset.desc;
    } else {
      materialDesc.textContent = "";
    }
  }

  materialSelect.addEventListener("change", updateMaterialDesc);
  loadMaterials();

  const segmentsBody = document.getElementById("segments-body");
  const featuresBox = document.getElementById("features-container");
  const empty = document.getElementById("no-features");
  const form = document.getElementById("planning-form");
  const errorBox = document.getElementById("form-error");
  const submit = document.getElementById("submit-button");
  let segmentNo = 0, featureNo = 0;

  const numOrNull = value => value === "" ? null : Number(value);
  let errorTimer = null;
  const showError = msg => {
    clearTimeout(errorTimer);
    errorBox.textContent = msg;
    errorBox.classList.remove("hidden");
    errorBox.scrollIntoView({behavior:"smooth", block:"center"});
    errorTimer = setTimeout(() => errorBox.classList.add("hidden"), 4000);
  };

  function updateTotal() {
    const total = [...segmentsBody.querySelectorAll(".seg-length")]
      .reduce((sum, el) => sum + (Number(el.value) || 0), 0);
    document.getElementById("total-length").textContent =
      `${Number(total.toFixed(3))} mm`;
  }

  function refreshSegmentOptions() {
    const count = segmentsBody.children.length;
    document.querySelectorAll(".feature-segment").forEach(select => {
      const old = select.value;
      select.innerHTML = Array.from({length: count}, (_, i) =>
        `<option value="${i + 1}">Seg ${i + 1}</option>`
      ).join("");
      if ([...select.options].some(o => o.value === old)) select.value = old;
    });
  }

  function addSegment(v = {}) {
    segmentNo++;
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><input class="seg-id" value="${v.segment_id || `S${String(segmentNo).padStart(2,"0")}`}" required></td>
      <td><input class="seg-dia" type="number" min="0.001" step="0.001" value="${v.diameter_mm ?? ""}" required></td>
      <td><input class="seg-length" type="number" min="0.001" step="0.001" value="${v.length_mm ?? ""}" required></td>
      <td><input class="seg-upper" type="number" step="0.001" value="${v.diameter_upper_deviation_mm ?? ""}"></td>
      <td><input class="seg-lower" type="number" step="0.001" value="${v.diameter_lower_deviation_mm ?? ""}"></td>
      <td><input class="seg-ra" type="number" min="0.001" step="any" value="${v.roughness_ra ?? ""}"></td>
      <td><input class="seg-area" type="number" min="0.001" step="any" value="${v.surface_area_mm2 ?? ""}" title="Cylindrical surface area"></td>
      <td><input class="seg-type" value="${v.segment_type ?? ""}" title="Segment classification (e.g. Rotor_Core_Fit)"></td>
      <td><button type="button" class="button danger">Delete</button></td>`;
    segmentsBody.appendChild(row);
    row.querySelector(".seg-length").addEventListener("input", updateTotal);
    row.querySelector("button").addEventListener("click", () => {
      if (segmentsBody.children.length === 1) return showError("At least one segment is required.");
      row.remove(); updateTotal(); refreshSegmentOptions();
      debouncePreview();
    });
    updateTotal(); refreshSegmentOptions();
    debouncePreview();
  }

  function specific(type) {
    if (type === "keyway") return `
      <div class="grid four">
        <label>Keyway Width (mm)<input class="keyway-width" type="number" min="0.001" step="0.001" required></label>
        <label>Keyway Depth (mm)<input class="keyway-depth" type="number" min="0.001" step="0.001" required></label>
        <label>Keyway Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
        <label>Keyway Type<select class="keyway-type">
          <option value="">Auto / Flat</option><option value="flat_key">Flat Key</option>
          <option value="profile_key">Profile Key</option><option value="wedge_key">Wedge Key</option>
        </select></label>
      </div>`;
    if (type === "hole") return `
      <div class="grid four">
        <label>Hole Diameter (mm)<input class="hole-dia" type="number" min="0.001" step="0.001" required></label>
        <label>Hole Type<select class="hole-type"><option value="through">Through</option><option value="blind">Blind</option></select></label>
        <label>Direction<select class="hole-direction"><option value="radial">Radial</option><option value="axial">Axial</option></select></label>
        <label class="hole-depth-wrap hidden">Blind Depth (mm)<input class="hole-depth" type="number" min="0.001" step="0.001"></label>
        <label>Hole Count<input class="hole-count" type="number" min="1" step="1" value="1"></label>
        <label>Hole Angle (°)<input class="hole-angle" type="number" min="0" max="359.9" step="1" title="First-hole angle for multiple holes at the same position"></label>
      </div>`;
    if (type === "bore") return `
      <div class="grid four">
        <label>Bore Diameter (mm)<input class="bore-dia" type="number" min="0.001" step="0.001" required></label>
        <label>Bore Length (mm)<input class="bore-length" type="number" min="0.001" step="0.001" required></label>
        <label>Bore Type<select class="bore-through"><option value="true">Through</option><option value="false">Blind</option></select></label>
        <label class="muted" style="font-size:12px">Position = bore start (left end)</label>
      </div>`;
    if (type === "flat") return `
      <div class="grid two">
        <label>Width Across Flats (mm)<input class="flat-width" type="number" min="0.001" step="0.001" required></label>
        <label>Flat Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
      </div>`;
    if (type === "thread") return `
      <div class="grid four">
        <label>Thread Spec<input class="thread-spec" placeholder="M10x1.5" required></label>
        <label>Hand<select class="thread-hand"><option value="right">Right-hand</option><option value="left">Left-hand</option></select></label>
        <label>Thread Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
        <label>Accuracy Grade<input class="thread-grade" placeholder="Optional, e.g. 6g"></label>
      </div>`;
    if (type === "knurl") return `
      <div class="grid two">
        <label>Knurl Type<select class="knurl-type"><option value="straight">Straight</option><option value="diamond">Diamond</option></select></label>
        <label>Knurl Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
      </div>`;
    if (type === "bearing_seat") return `
      <div class="grid three">
        <label>Bearing Seat Diameter (mm)<input class="bearing-dia" type="number" min="0.001" step="0.001" required></label>
        <label>Tolerance<select class="bearing-tolerance">
          <option value="IT5">IT5 (Precision)</option><option value="IT6" selected>IT6 (Standard)</option><option value="IT7">IT7 (Normal)</option>
        </select></label>
        <label>Seat Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
      </div>`;
    if (type === "spline") return `
      <div class="grid four">
        <label>Spline Type<select class="spline-type"><option value="involute">Involute</option><option value="straight">Straight</option></select></label>
        <label>Number of Teeth<input class="spline-teeth" type="number" min="4" step="1" required></label>
        <label>Module (mm)<input class="spline-module" type="number" min="0.1" step="0.1" required></label>
        <label>Spline Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
        <label>Major Dia (mm)<input class="spline-major-dia" type="number" min="0.001" step="0.001"></label>
        <label>Minor Dia (mm)<input class="spline-minor-dia" type="number" min="0.001" step="0.001"></label>
        <label>Pressure Angle (°)<input class="spline-pressure" type="number" min="0" step="0.1"></label>
        <label>Key Width B (mm)<input class="spline-keywidth" type="number" min="0.001" step="0.001"></label>
      </div>`;
    if (type === "taper") return `
      <div class="grid three">
        <label>Taper Ratio (1:N)<input class="taper-ratio" type="number" min="1" step="0.5" placeholder="e.g. 10" required></label>
        <label>Large Diameter (mm)<input class="taper-large-dia" type="number" min="0.001" step="0.001" required></label>
        <label>Taper Length (mm)<input class="taper-length" type="number" min="0.001" step="0.001" required></label>
      </div>`;
    if (type === "groove") return `
      <div class="grid three">
        <label>Groove Type<select class="groove-type">
          <option value="snap_ring">Snap Ring</option><option value="thread_relief">Thread Relief</option>
          <option value="undercut">Undercut</option><option value="seal">Seal Groove</option>
        </select></label>
        <label>Width (mm)<input class="groove-width" type="number" min="0.1" step="0.1" required></label>
        <label>Depth (mm)<input class="groove-depth" type="number" min="0.1" step="0.1" required></label>
      </div>`;
    if (type === "seal_area") return `
      <div class="grid three">
        <label>Seal Type<select class="seal-type">
          <option value="rubber">Rubber Lip Seal</option><option value="mechanical">Mechanical Seal</option><option value="labyrinth">Labyrinth Seal</option>
        </select></label>
        <label>Seal Diameter (mm)<input class="seal-dia" type="number" min="0.001" step="0.001" required></label>
        <label>Seal Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
      </div>`;
    if (type === "gear_teeth") return `
      <div class="grid four">
        <label>Module (mm)<input class="gear-module" type="number" min="0.1" step="0.1" required></label>
        <label>Number of Teeth<input class="gear-teeth" type="number" min="8" step="1" required></label>
        <label>Pressure Angle<select class="gear-pressure"><option value="20" selected>20°</option><option value="14.5">14.5°</option><option value="25">25°</option></select></label>
        <label>Face Width (mm)<input class="gear-face-width" type="number" min="0.1" step="0.1" required></label>
        <label>Gear Type<select class="gear-type"><option value="spur">Spur</option><option value="helical">Helical</option></select></label>
        <label>Helix Angle (°)<input class="gear-helix" type="number" min="0" step="any" value="0"></label>
        <label>Tooth Height (mm)<input class="gear-tooth-height" type="number" min="0.001" step="0.001"></label>
        <label>Outer Dia (mm)<input class="gear-outer-dia" type="number" min="0.001" step="0.001" title="Gear tip/outer diameter (rough-turning reference)"></label>
        <label>Root Dia (mm)<input class="gear-root-dia" type="number" min="0.001" step="0.001" title="Gear root diameter (hobbing reference)"></label>
        <label>Post-Heat Finish<input class="gear-finish" type="checkbox" title="Whether the gear needs post-heat-treatment finishing (grinding / hard hobbing)"></label>
      </div>`;
    if (type === "flange") return `
      <div class="grid three">
        <label>Flange Diameter (mm)<input class="flange-dia" type="number" min="0.001" step="0.001" required></label>
        <label>Flange Thickness (mm)<input class="flange-thickness" type="number" min="0.1" step="0.1" required></label>
        <label>Number of Holes<input class="flange-holes" type="number" min="0" step="1" value="0"></label>
      </div>`;
    if (type === "cam") return `
      <div class="grid four">
        <label>Cam Type<select class="cam-type"><option value="grinding">Ground (precision)</option><option value="milling">Milled</option></select></label>
        <label>Number of Lobes<input class="cam-lobe-count" type="number" min="1" step="1" value="4"></label>
        <label>Base Circle Dia (mm)<input class="cam-base-dia" type="number" min="0.001" step="0.001"></label>
        <label>Cam Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
        <label>Lobe Lift (mm)<input class="cam-lobe-lift" type="number" min="0.001" step="0.001"></label>
      </div>`;
    if (type === "worm") return `
      <div class="grid four">
        <label>Module (mm)<input class="worm-module" type="number" min="0.1" step="0.1"></label>
        <label>Number of Starts<input class="worm-starts" type="number" min="1" step="1" value="1"></label>
        <label>Pressure Angle (°)<input class="worm-pressure" type="number" min="0" step="0.1"></label>
        <label>Worm Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
        <label>Outer Dia (mm)<input class="worm-outer-dia" type="number" min="0.001" step="0.001"></label>
      </div>`;
    if (type === "crank_pin") return `
      <div class="grid four">
        <label>Pin Diameter (mm)<input class="crank-dia" type="number" min="0.001" step="0.001"></label>
        <label>Pin Width (mm)<input class="crank-width" type="number" min="0.001" step="0.001"></label>
        <label>Crank Offset (mm)<input class="crank-offset" type="number" min="0" step="0.001"></label>
        <label>Pin Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001" required></label>
      </div>`;
    return `<div class="grid two"><label>Feature Length (mm)<input class="feature-length" type="number" min="0.001" step="0.001"></label></div>`;
  }

  function bindSpecific(card) {
    const type = card.querySelector(".hole-type");
    if (type) {
      const update = () => card.querySelector(".hole-depth-wrap")
        .classList.toggle("hidden", type.value !== "blind");
      type.addEventListener("change", update); update();
    }
  }

  function addFeature(v = {}) {
    featureNo++;
    const card = document.createElement("div");
    card.className = "feature-card";
    card.innerHTML = `
      <div class="feature-title">
        <h3>Feature <input class="feature-id" value="${v.feature_id || `F${String(featureNo).padStart(2,"0")}`}" style="width:110px;display:inline-block"></h3>
        <button type="button" class="button danger">Delete</button>
      </div>
      <div class="grid four">
        <label>Feature Type<select class="feature-type">
          <option value="keyway">Keyway</option><option value="hole">Hole</option><option value="flat">Flat</option>
          <option value="thread">Thread</option><option value="knurl">Knurl</option>
          <option value="bearing_seat">Bearing Seat</option><option value="spline">Spline</option>
          <option value="taper">Taper</option><option value="groove">Groove</option>
          <option value="seal_area">Seal Area</option><option value="gear_teeth">Gear Teeth</option>
          <option value="flange">Flange</option><option value="bore">Bore</option>
          <option value="cam">Cam</option><option value="worm">Worm</option><option value="crank_pin">Crank Pin</option>
        </select></label>
        <label>Positioning<select class="position-mode"><option value="segment_relative">Segment Relative</option><option value="global_absolute">Global Absolute</option></select></label>
        <label class="segment-field">Segment<select class="feature-segment"></select></label>
        <label class="segment-field">Offset from Left (mm)<input class="segment-offset" type="number" min="0" step="0.001" value="0"></label>
        <label class="global-field hidden">Global Position (mm)<input class="global-position" type="number" min="0" step="0.001" value="0"></label>
        <label>Upper Dev (mm)<input class="feature-upper" type="number" step="0.001"></label>
        <label>Lower Dev (mm)<input class="feature-lower" type="number" step="0.001"></label>
        <label>Roughness Ra (μm)<input class="feature-ra" type="number" min="0.001" step="any"></label>
      </div>
      <div class="feature-specific">${specific(v.feature_type || "keyway")}</div>`;

    featuresBox.appendChild(card); empty.classList.add("hidden");
    refreshSegmentOptions();
    card.querySelector(".feature-type").value = v.feature_type || "keyway";
    card.querySelector(".position-mode").value = v.positioning_mode || "segment_relative";
    if (v.segment_index) card.querySelector(".feature-segment").value = String(v.segment_index);
    card.querySelector(".segment-offset").value = v.segment_offset_mm ?? 0;
    card.querySelector(".global-position").value = v.global_position_mm ?? 0;
    card.querySelector(".feature-upper").value = v.tolerance_upper_mm ?? "";
    card.querySelector(".feature-lower").value = v.tolerance_lower_mm ?? "";
    card.querySelector(".feature-ra").value = v.roughness_ra ?? "";

    const updateMode = () => {
      const global = card.querySelector(".position-mode").value === "global_absolute";
      card.querySelectorAll(".segment-field").forEach(x => x.classList.toggle("hidden", global));
      card.querySelector(".global-field").classList.toggle("hidden", !global);
    };
    card.querySelector(".position-mode").addEventListener("change", updateMode);
    card.querySelector(".feature-type").addEventListener("change", e => {
      card.querySelector(".feature-specific").innerHTML = specific(e.target.value);
      bindSpecific(card);
    });
    card.querySelector(".feature-title button").addEventListener("click", () => {
      card.remove();
      if (!featuresBox.children.length) empty.classList.remove("hidden");
      debouncePreview();
    });
    bindSpecific(card); updateMode();

    // Example values
    if (v.feature_type === "keyway") {
      card.querySelector(".keyway-width").value = v.keyway_width_mm ?? "";
      card.querySelector(".keyway-depth").value = v.keyway_depth_mm ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
      card.querySelector(".keyway-type").value = v.keyway_type || "";
    } else if (v.feature_type === "hole") {
      card.querySelector(".hole-dia").value = v.hole_diameter_mm ?? "";
      card.querySelector(".hole-type").value = v.hole_type || "through";
      card.querySelector(".hole-direction").value = v.hole_direction || "radial";
      card.querySelector(".hole-count").value = v.hole_count ?? 1;
      card.querySelector(".hole-angle").value = v.hole_angle_deg ?? "";
      bindSpecific(card);
    } else if (v.feature_type === "bore") {
      card.querySelector(".bore-dia").value = v.bore_diameter_mm ?? "";
      card.querySelector(".bore-length").value = v.bore_length_mm ?? "";
      card.querySelector(".bore-through").value = v.bore_through ? "true" : "false";
    } else if (v.feature_type === "flat") {
      card.querySelector(".flat-width").value = v.flat_width_mm ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
    } else if (v.feature_type === "thread") {
      card.querySelector(".thread-spec").value = v.thread_specification ?? "";
      card.querySelector(".thread-hand").value = v.thread_handedness || "right";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
      card.querySelector(".thread-grade").value = v.thread_accuracy_grade ?? "";
    } else if (v.feature_type === "knurl") {
      card.querySelector(".knurl-type").value = v.knurl_type || "straight";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
    } else if (v.feature_type === "bearing_seat") {
      card.querySelector(".bearing-dia").value = v.bearing_seat_diameter_mm ?? "";
      card.querySelector(".bearing-tolerance").value = v.bearing_seat_tolerance || "IT6";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
    } else if (v.feature_type === "spline") {
      card.querySelector(".spline-type").value = v.spline_type || "involute";
      card.querySelector(".spline-teeth").value = v.spline_teeth ?? "";
      card.querySelector(".spline-module").value = v.spline_module ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
      card.querySelector(".spline-major-dia").value = v.spline_major_diameter_mm ?? "";
      card.querySelector(".spline-minor-dia").value = v.spline_minor_diameter_mm ?? "";
      card.querySelector(".spline-pressure").value = v.spline_pressure_angle_deg ?? "";
      card.querySelector(".spline-keywidth").value = v.spline_key_width_mm ?? "";
    } else if (v.feature_type === "taper") {
      card.querySelector(".taper-ratio").value = v.taper_ratio ?? "";
      card.querySelector(".taper-large-dia").value = v.taper_large_diameter_mm ?? "";
      card.querySelector(".taper-length").value = v.taper_length_mm ?? "";
    } else if (v.feature_type === "groove") {
      card.querySelector(".groove-type").value = v.groove_type || "snap_ring";
      card.querySelector(".groove-width").value = v.groove_width_mm ?? "";
      card.querySelector(".groove-depth").value = v.groove_depth_mm ?? "";
    } else if (v.feature_type === "seal_area") {
      card.querySelector(".seal-type").value = v.seal_type || "rubber";
      card.querySelector(".seal-dia").value = v.seal_diameter_mm ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
    } else if (v.feature_type === "gear_teeth") {
      card.querySelector(".gear-module").value = v.gear_module ?? "";
      card.querySelector(".gear-teeth").value = v.gear_teeth ?? "";
      card.querySelector(".gear-pressure").value = v.gear_pressure_angle || "20";
      card.querySelector(".gear-face-width").value = v.gear_face_width_mm ?? "";
      card.querySelector(".gear-type").value = v.gear_type || "spur";
      card.querySelector(".gear-helix").value = v.helix_angle_deg ?? 0;
      card.querySelector(".gear-tooth-height").value = v.gear_tooth_height_mm ?? "";
      card.querySelector(".gear-outer-dia").value = v.gear_outer_diameter_mm ?? "";
      card.querySelector(".gear-root-dia").value = v.gear_root_diameter_mm ?? "";
      card.querySelector(".gear-finish").checked = v.gear_finish_required === true;
    } else if (v.feature_type === "flange") {
      card.querySelector(".flange-dia").value = v.flange_diameter_mm ?? "";
      card.querySelector(".flange-thickness").value = v.flange_thickness_mm ?? "";
      card.querySelector(".flange-holes").value = v.flange_holes ?? "0";
    } else if (v.feature_type === "cam") {
      card.querySelector(".cam-type").value = v.cam_type || "grinding";
      card.querySelector(".cam-lobe-count").value = v.cam_lobe_count ?? 4;
      card.querySelector(".cam-base-dia").value = v.cam_base_circle_diameter_mm ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
      card.querySelector(".cam-lobe-lift").value = v.cam_lobe_lift_mm ?? "";
    } else if (v.feature_type === "worm") {
      card.querySelector(".worm-module").value = v.worm_module ?? "";
      card.querySelector(".worm-starts").value = v.worm_starts ?? 1;
      card.querySelector(".worm-pressure").value = v.worm_pressure_angle_deg ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
      card.querySelector(".worm-outer-dia").value = v.worm_outer_diameter_mm ?? "";
    } else if (v.feature_type === "crank_pin") {
      card.querySelector(".crank-dia").value = v.crank_pin_diameter_mm ?? "";
      card.querySelector(".crank-width").value = v.crank_pin_width_mm ?? "";
      card.querySelector(".crank-offset").value = v.crank_offset_mm ?? "";
      card.querySelector(".feature-length").value = v.feature_length_mm ?? "";
    }
    debouncePreview();
  }

  function collectSegments() {
    return [...segmentsBody.querySelectorAll("tr")].map(row => ({
      segment_id: row.querySelector(".seg-id").value.trim(),
      diameter_mm: Number(row.querySelector(".seg-dia").value),
      length_mm: Number(row.querySelector(".seg-length").value),
      diameter_upper_deviation_mm: numOrNull(row.querySelector(".seg-upper").value),
      diameter_lower_deviation_mm: numOrNull(row.querySelector(".seg-lower").value),
      roughness_ra: numOrNull(row.querySelector(".seg-ra").value),
      surface_area_mm2: numOrNull(row.querySelector(".seg-area").value),
      segment_type: row.querySelector(".seg-type").value.trim() || null,
    }));
  }

  function collectFeatures() {
    return [...featuresBox.querySelectorAll(".feature-card")].map(card => {
      const type = card.querySelector(".feature-type").value;
      const mode = card.querySelector(".position-mode").value;
      const data = {
        feature_id: card.querySelector(".feature-id").value.trim(),
        feature_type: type,
        positioning_mode: mode,
        segment_index: mode === "segment_relative" ? Number(card.querySelector(".feature-segment").value) : null,
        segment_offset_mm: mode === "segment_relative" ? Number(card.querySelector(".segment-offset").value) : null,
        global_position_mm: mode === "global_absolute" ? Number(card.querySelector(".global-position").value) : null,
        tolerance_upper_mm: numOrNull(card.querySelector(".feature-upper").value),
        tolerance_lower_mm: numOrNull(card.querySelector(".feature-lower").value),
        roughness_ra: numOrNull(card.querySelector(".feature-ra").value),
        processing_timing: "undecided",
      };
      const length = card.querySelector(".feature-length");
      if (length) data.feature_length_mm = Number(length.value);

      if (type === "keyway") {
        data.keyway_width_mm = Number(card.querySelector(".keyway-width").value);
        data.keyway_depth_mm = Number(card.querySelector(".keyway-depth").value);
        data.keyway_type = card.querySelector(".keyway-type").value || null;
      } else if (type === "hole") {
        data.hole_diameter_mm = Number(card.querySelector(".hole-dia").value);
        data.hole_type = card.querySelector(".hole-type").value;
        data.hole_direction = card.querySelector(".hole-direction").value;
        data.hole_depth_mm = data.hole_type === "blind" ? Number(card.querySelector(".hole-depth").value) : null;
        const hc = Number(card.querySelector(".hole-count").value);
        data.hole_count = hc > 1 ? hc : null;
        data.hole_angle_deg = numOrNull(card.querySelector(".hole-angle").value);
      } else if (type === "bore") {
        data.bore_diameter_mm = Number(card.querySelector(".bore-dia").value);
        data.bore_length_mm = Number(card.querySelector(".bore-length").value);
        data.bore_through = card.querySelector(".bore-through").value === "true";
      } else if (type === "flat") {
        data.flat_width_mm = Number(card.querySelector(".flat-width").value);
      } else if (type === "thread") {
        data.thread_specification = card.querySelector(".thread-spec").value.trim();
        data.thread_handedness = card.querySelector(".thread-hand").value;
        data.thread_accuracy_grade = card.querySelector(".thread-grade").value.trim() || null;
      } else if (type === "knurl") {
        data.knurl_type = card.querySelector(".knurl-type").value;
      } else if (type === "bearing_seat") {
        data.bearing_seat_diameter_mm = Number(card.querySelector(".bearing-dia").value);
        data.bearing_seat_tolerance = card.querySelector(".bearing-tolerance").value;
      } else if (type === "spline") {
        data.spline_type = card.querySelector(".spline-type").value;
        data.spline_teeth = Number(card.querySelector(".spline-teeth").value);
        data.spline_module = Number(card.querySelector(".spline-module").value);
        data.spline_major_diameter_mm = numOrNull(card.querySelector(".spline-major-dia").value);
        data.spline_minor_diameter_mm = numOrNull(card.querySelector(".spline-minor-dia").value);
        data.spline_pressure_angle_deg = numOrNull(card.querySelector(".spline-pressure").value);
        data.spline_key_width_mm = numOrNull(card.querySelector(".spline-keywidth").value);
      } else if (type === "taper") {
        data.taper_ratio = Number(card.querySelector(".taper-ratio").value);
        data.taper_large_diameter_mm = Number(card.querySelector(".taper-large-dia").value);
        data.taper_length_mm = Number(card.querySelector(".taper-length").value);
      } else if (type === "groove") {
        data.groove_type = card.querySelector(".groove-type").value;
        data.groove_width_mm = Number(card.querySelector(".groove-width").value);
        data.groove_depth_mm = Number(card.querySelector(".groove-depth").value);
      } else if (type === "seal_area") {
        data.seal_type = card.querySelector(".seal-type").value;
        data.seal_diameter_mm = Number(card.querySelector(".seal-dia").value);
      } else if (type === "gear_teeth") {
        data.gear_module = Number(card.querySelector(".gear-module").value);
        data.gear_teeth = Number(card.querySelector(".gear-teeth").value);
        data.gear_pressure_angle = Number(card.querySelector(".gear-pressure").value);
        data.gear_face_width_mm = Number(card.querySelector(".gear-face-width").value);
        data.gear_type = card.querySelector(".gear-type").value;
        data.helix_angle_deg = numOrNull(card.querySelector(".gear-helix").value);
        data.gear_tooth_height_mm = numOrNull(card.querySelector(".gear-tooth-height").value);
        data.gear_outer_diameter_mm = numOrNull(card.querySelector(".gear-outer-dia").value);
        data.gear_root_diameter_mm = numOrNull(card.querySelector(".gear-root-dia").value);
        data.gear_finish_required = card.querySelector(".gear-finish").checked;
      } else if (type === "flange") {
        data.flange_diameter_mm = Number(card.querySelector(".flange-dia").value);
        data.flange_thickness_mm = Number(card.querySelector(".flange-thickness").value);
        data.flange_holes = Number(card.querySelector(".flange-holes").value);
      } else if (type === "cam") {
        data.cam_type = card.querySelector(".cam-type").value;
        data.cam_lobe_count = Number(card.querySelector(".cam-lobe-count").value);
        data.cam_base_circle_diameter_mm = numOrNull(card.querySelector(".cam-base-dia").value);
        data.cam_lobe_lift_mm = numOrNull(card.querySelector(".cam-lobe-lift").value);
      } else if (type === "worm") {
        data.worm_module = numOrNull(card.querySelector(".worm-module").value);
        data.worm_starts = numOrNull(card.querySelector(".worm-starts").value);
        data.worm_pressure_angle_deg = numOrNull(card.querySelector(".worm-pressure").value);
        data.worm_outer_diameter_mm = numOrNull(card.querySelector(".worm-outer-dia").value);
      } else if (type === "crank_pin") {
        data.crank_pin_diameter_mm = numOrNull(card.querySelector(".crank-dia").value);
        data.crank_pin_width_mm = numOrNull(card.querySelector(".crank-width").value);
        data.crank_offset_mm = numOrNull(card.querySelector(".crank-offset").value);
      }
      return data;
    });
  }

  function collectGlobalRequirements() {
    return {
      heat_treatment: document.getElementById("heat-treatment").value,
      heat_treatment_note: document.getElementById("heat-treatment-note").value.trim() || null,
      target_hardness_hrc: numOrNull(document.getElementById("target-hardness").value),
      case_depth_mm: numOrNull(document.getElementById("case-depth").value),
      blank_condition: document.getElementById("blank-condition").value,
      pre_heat_treatment: document.getElementById("pre-heat-treatment").value,
      surface_treatment: "none",
      batch_quantity: 1,
    };
  }

  document.getElementById("add-segment").addEventListener("click", () => addSegment());
  document.getElementById("add-feature").addEventListener("click", () => addFeature());

  // Process Route Preview: collapsible inline panel (expanded on demand, never blocks the form)
  const previewToggle = document.getElementById("preview-toggle");
  const previewPanel = document.getElementById("preview-panel");
  if (previewToggle && previewPanel) {
    previewToggle.addEventListener("click", () => {
      const collapsing = !previewPanel.classList.contains("hidden");
      previewPanel.classList.toggle("hidden", collapsing);
      previewToggle.classList.toggle("open", !collapsing);
      previewToggle.textContent = collapsing ? "▸ Process Route Preview" : "▾ Hide Route Preview";
      if (!collapsing) debouncePreview();
    });
  }

  document.getElementById("load-example").addEventListener("click", () => {
    segmentsBody.innerHTML = ""; featuresBox.innerHTML = "";
    segmentNo = 0; featureNo = 0;
    [
      {segment_id:"S01",diameter_mm:60,length_mm:80,diameter_upper_deviation_mm:.03,diameter_lower_deviation_mm:-.03,roughness_ra:1.6},
      {segment_id:"S02",diameter_mm:50,length_mm:60,diameter_upper_deviation_mm:.03,diameter_lower_deviation_mm:-.03,roughness_ra:1.6},
      {segment_id:"S03",diameter_mm:45,length_mm:40,diameter_upper_deviation_mm:.05,diameter_lower_deviation_mm:-.05,roughness_ra:3.2}
    ].forEach(addSegment);
    addFeature({feature_id:"F01",feature_type:"keyway",positioning_mode:"segment_relative",segment_index:2,segment_offset_mm:15,tolerance_upper_mm:.03,tolerance_lower_mm:-.03,roughness_ra:1.6,keyway_width_mm:8,keyway_depth_mm:4,feature_length_mm:30});
    addFeature({feature_id:"F02",feature_type:"hole",positioning_mode:"segment_relative",segment_index:1,segment_offset_mm:40,tolerance_upper_mm:.05,tolerance_lower_mm:-.05,roughness_ra:3.2,hole_diameter_mm:6,hole_type:"through",hole_direction:"radial"});
    materialSelect.value = "45";
    updateMaterialDesc();
    document.getElementById("blank-diameter").value = "65";
  });

  form.addEventListener("submit", async event => {
    event.preventDefault(); errorBox.classList.add("hidden");
    if (!form.reportValidity()) return;
    const segments = collectSegments();
    const blankDia = Number(document.getElementById("blank-diameter").value);
    const maxSegDia = segments.reduce((max, s) => Math.max(max, s.diameter_mm), 0);
    if (blankDia < maxSegDia) {
      showError(`Blank diameter (${blankDia} mm) must be ≥ max segment diameter (${maxSegDia} mm).`);
      return;
    }
    const payload = {
      material: materialSelect.value.trim(),
      blank_type: document.getElementById("blank-type").value,
      blank_diameter_mm: blankDia,
      blank_inner_diameter_mm: numOrNull(document.getElementById("blank-inner-diameter").value),
      segments: segments,
      features: collectFeatures(),
      global_requirements: collectGlobalRequirements(),
    };
    submit.disabled = true; submit.textContent = "Submitting...";
    try {
      const response = await fetch("/api/jobs", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      const data = await response.json();
      if (!response.ok) throw new Error(JSON.stringify(data.detail || data));

      location.href = `/jobs/${data.job_id}`;
    } catch (error) {
      let msg = error.message;
      if (error.name === "TypeError") {
        msg = "Unable to connect to server. Please confirm ShaftPlanner is running (execute python start_shaftplanner.py in terminal).";
      }
      showError(`Submission failed: ${msg}`);
      submit.disabled = false; submit.textContent = "Start Process Planning";
    }
  });

  // Load case from URL parameter
  async function loadCaseFromUrl() {
    const caseId = window.CASE_ID_FROM_URL;
    if (!caseId) {
      // No case specified, load default segment
      addSegment({segment_id:"S01",diameter_mm:60,length_mm:180});
      return;
    }

    try {
      const response = await fetch(`/api/cases/${caseId}`);
      if (!response.ok) throw new Error('Case not found');
      const data = await response.json();
      const caseData = data.case;

      // Show case banner
      const banner = document.getElementById('case-banner');
      if (banner) {
        banner.style.display = 'flex';
        document.getElementById('case-banner-name').textContent = `Based on: ${caseData.part_name}`;
        document.getElementById('case-banner-desc').textContent = `${caseData.industry} | ${caseData.material} | ${caseData.tolerance || '-'}`;
        document.getElementById('case-banner-link').href = `/cases/${caseId}`;
      }

      // Clear existing data
      segmentsBody.innerHTML = "";
      featuresBox.innerHTML = "";
      segmentNo = 0;
      featureNo = 0;

      // Set material
      if (caseData.material) {
        // Find matching material option
        const options = materialSelect.options;
        for (let i = 0; i < options.length; i++) {
          if (options[i].value === caseData.material || options[i].textContent.includes(caseData.material)) {
            materialSelect.value = options[i].value;
            break;
          }
        }
        updateMaterialDesc();
      }

      const heatTreatmentMap = {
        "Normalizing": "normalizing",
        "Quench and Temper": "quench_temper",
        "Carburize and Quench": "carburize_quench",
        "Nitriding": "nitriding",
        "Induction Hardening": "induction_hardening",
      };
      if (heatTreatmentMap[caseData.heat_treatment]) {
        document.getElementById("heat-treatment").value = heatTreatmentMap[caseData.heat_treatment];
      }

      // Generate segments: prefer stored segments, else derive from case data
      if (caseData.segments && caseData.segments.length > 0) {
        caseData.segments.forEach(seg => addSegment(seg));
      } else if (caseData.diameter_mm && caseData.length_mm) {
        const mainDia = caseData.diameter_mm;
        const totalLength = caseData.length_mm;
        const tol = caseData.tolerance || 'IT6';
        const ra = caseData.surface_roughness || 'Ra1.6';

        // Derive tolerance from IT grade
        const tolMap = { 'IT5': 0.01, 'IT6': 0.02, 'IT7': 0.03 };
        const halfTol = (tolMap[tol] || 0.02);
        const raMap = { 'Ra0.4': 0.4, 'Ra0.8': 0.8, 'Ra1.6': 1.6, 'Ra3.2': 3.2 };
        const raVal = raMap[ra] || 1.6;

        const seg1Length = Math.round(totalLength * 0.3);
        const seg2Length = Math.round(totalLength * 0.4);
        const seg3Length = totalLength - seg1Length - seg2Length;

        addSegment({
          segment_id: "S01", diameter_mm: mainDia + 5, length_mm: seg1Length,
          diameter_upper_deviation_mm: halfTol + 0.01,
          diameter_lower_deviation_mm: -(halfTol + 0.01),
          roughness_ra: Math.min(raVal * 2, 3.2)
        });
        addSegment({
          segment_id: "S02", diameter_mm: mainDia, length_mm: seg2Length,
          diameter_upper_deviation_mm: halfTol,
          diameter_lower_deviation_mm: -halfTol,
          roughness_ra: raVal
        });
        addSegment({
          segment_id: "S03", diameter_mm: mainDia - 5, length_mm: seg3Length,
          diameter_upper_deviation_mm: halfTol + 0.03,
          diameter_lower_deviation_mm: -(halfTol + 0.03),
          roughness_ra: Math.min(raVal * 2, 6.3)
        });
      } else {
        addSegment({segment_id:"S01",diameter_mm:60,length_mm:180});
      }

      // Set blank diameter based on actual max segment diameter
      const maxSegDia = [...segmentsBody.querySelectorAll("tr")].reduce((max, row) => {
        return Math.max(max, Number(row.querySelector(".seg-dia")?.value) || 0);
      }, 0);
      if (maxSegDia > 0) {
        const blankDia = Math.ceil(maxSegDia * 1.15 / 5) * 5; // 15% margin, round up to nearest 5
        document.getElementById("blank-diameter").value = Math.max(blankDia, maxSegDia);
      }

      // Helper: Get segment length by index (1-based)
      const getSegmentLength = (idx) => {
        const rows = segmentsBody.querySelectorAll("tr");
        if (idx < 1 || idx > rows.length) return 100; // default
        return Number(rows[idx - 1].querySelector(".seg-length").value) || 100;
      };

      // Helper: Safe feature parameter calculation
      const safeFeatureParams = (segIdx, offsetPercent, lengthPercent) => {
        const segLen = getSegmentLength(segIdx);
        const maxOffset = Math.round(segLen * 0.8); // Max 80% into segment
        const offset = Math.min(Math.round(segLen * offsetPercent), maxOffset);
        const maxLen = segLen - offset - 2; // Leave 2mm margin
        const length = Math.min(Math.round(segLen * lengthPercent), Math.max(maxLen, 5));
        return { offset, length };
      };

      // Generate features: prefer stored features, else derive from main_features
      if (caseData.features && caseData.features.length > 0) {
        caseData.features.forEach(f => addFeature(f));
      } else if (caseData.main_features && caseData.main_features.length) {
        let featureIdx = 1;
        caseData.main_features.forEach(feature => {
          const featureLower = feature.toLowerCase();
          let featureData = null;

          if (featureLower.includes('keyway')) {
            const params = safeFeatureParams(2, 0.2, 0.25);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "keyway",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              keyway_width_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.25 : 6),
              keyway_depth_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.15 : 3),
              keyway_type: "profile_key",
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('thread')) {
            const params = safeFeatureParams(1, 0.1, 0.5);
            // Shaft-end thread diameter ~0.6x main diameter, rounded to 5 (should not equal the main shaft diameter)
            const threadDia = Math.max(8, Math.round((caseData.diameter_mm || 16) * 0.6 / 5) * 5);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "thread",
              positioning_mode: "segment_relative",
              segment_index: 1,
              segment_offset_mm: params.offset,
              thread_specification: `M${threadDia}x1.5`,
              thread_handedness: "right",
              thread_accuracy_grade: "6g",
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('spline')) {
            const params = safeFeatureParams(2, 0.2, 0.4);
            const splineMajor = Math.round(caseData.diameter_mm || 20);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "spline",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              spline_type: "involute",
              spline_teeth: Math.round(caseData.diameter_mm ? caseData.diameter_mm / 2 : 10),
              spline_module: 2,
              spline_major_diameter_mm: splineMajor,
              spline_minor_diameter_mm: Math.max(8, splineMajor - 4),
              spline_pressure_angle_deg: 30,
              spline_key_width_mm: 3, // πm/2 ≈ 3
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('hole') || featureLower.includes('bore')) {
            const params = safeFeatureParams(2, 0.3, 0);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "hole",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              hole_diameter_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.3 : 8),
              hole_type: "through",
              hole_direction: "radial"
            };
          } else if (featureLower.includes('bearing') || featureLower.includes('journal') || featureLower.includes('rotor seat') || featureLower.includes('roller seat')) {
            const params = safeFeatureParams(2, 0.1, 0.3);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "bearing_seat",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              bearing_seat_diameter_mm: caseData.diameter_mm || 30,
              bearing_seat_tolerance: "IT6",
              tolerance_upper_mm: 0.01,
              tolerance_lower_mm: -0.01,
              roughness_ra: 0.8,
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('taper')) {
            const params = safeFeatureParams(1, 0, 0.6);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "taper",
              positioning_mode: "segment_relative",
              segment_index: 1,
              segment_offset_mm: params.offset,
              taper_ratio: 10,
              taper_large_diameter_mm: caseData.diameter_mm || 30,
              taper_length_mm: params.length
            };
          } else if (featureLower.includes('groove') || featureLower.includes('snap ring')) {
            const params = safeFeatureParams(2, 0.3, 0);
            // Scale groove width/depth by shaft diameter so small shafts do not get oversized snap-ring grooves
            const grooveWidth = Math.max(2, Math.round((caseData.diameter_mm || 30) * 0.05));
            const grooveDepth = Math.max(1, Math.round((caseData.diameter_mm || 30) * 0.025));
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "groove",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              groove_type: featureLower.includes('snap') ? "snap_ring" : "undercut",
              groove_width_mm: grooveWidth,
              groove_depth_mm: grooveDepth
            };
          } else if (featureLower.includes('flat') || featureLower.includes('d flat')) {
            const params = safeFeatureParams(2, 0.2, 0.3);
            // Flat across-flats ≈ 0.8x shaft diameter (flat depth ~20% of diameter), avoiding an over-deep 0.5x flat
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "flat",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              flat_width_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.8 : 24),
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('oil hole') || featureLower.includes('balancing hole')) {
            const params = safeFeatureParams(2, 0.3, 0);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "hole",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              hole_diameter_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.15 : 5),
              hole_type: "through",
              hole_direction: "radial"
            };
          } else if (featureLower.includes('seal')) {
            const params = safeFeatureParams(1, 0.05, 0.3);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "seal_area",
              positioning_mode: "segment_relative",
              segment_index: 1,
              segment_offset_mm: params.offset,
              seal_type: "rubber",
              seal_diameter_mm: caseData.diameter_mm || 30,
              roughness_ra: 0.8,
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('gear') || featureLower.includes('teeth') || featureLower.includes('helical') || featureLower.includes('timing gear')) {
            const params = safeFeatureParams(2, 0.2, 0.3);
            const isHelical = featureLower.includes('helical');
            const pitchDia = Math.round(caseData.diameter_mm || 20);
            // m=2 → tip diameter = pitch + 2m, root diameter = pitch - 2.5m, tooth height = 2.25m
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "gear_teeth",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              gear_module: 2,
              gear_teeth: Math.round(pitchDia / 2),
              gear_pressure_angle: 20,
              gear_face_width_mm: params.length,
              gear_type: isHelical ? "helical" : "spur",
              helix_angle_deg: isHelical ? 15 : 0,
              gear_outer_diameter_mm: pitchDia + 4,
              gear_root_diameter_mm: Math.max(8, pitchDia - 5),
              gear_tooth_height_mm: 4.5,
              gear_finish_required: isHelical
            };
          } else if (featureLower.includes('flange')) {
            const params = safeFeatureParams(1, 0, 0);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "flange",
              positioning_mode: "segment_relative",
              segment_index: 1,
              segment_offset_mm: params.offset,
              flange_diameter_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 2 : 60),
              flange_thickness_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.3 : 10),
              flange_holes: 4
            };
          } else if (featureLower.includes('encoder mount') || featureLower.includes('rotor seat') || featureLower.includes('impeller mount') || featureLower.includes('spindle nose') || featureLower.includes('universal joint')) {
            const params = safeFeatureParams(2, 0.2, 0.3);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "bearing_seat",
              positioning_mode: "segment_relative",
              segment_index: 2,
              segment_offset_mm: params.offset,
              bearing_seat_diameter_mm: caseData.diameter_mm || 30,
              bearing_seat_tolerance: "IT6",
              feature_length_mm: params.length
            };
          } else if (featureLower.includes('counterweight')) {
            // Counterweight/balance disc → flange (disc structure with evenly spaced holes)
            const params = safeFeatureParams(1, 0, 0);
            featureData = {
              feature_id: `F${String(featureIdx).padStart(2,"0")}`,
              feature_type: "flange",
              positioning_mode: "segment_relative",
              segment_index: 1,
              segment_offset_mm: params.offset,
              flange_diameter_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 1.5 : 45),
              flange_thickness_mm: Math.round(caseData.diameter_mm ? caseData.diameter_mm * 0.25 : 8),
              flange_holes: 4
            };
          }
          // cam / crank / eccentric have no suitable peagent feature type (not tapers); skip rather than emit a wrong feature

          if (featureData) {
            addFeature(featureData);
            featureIdx++;
          }
        });
      }

      // If no features were added, add a default keyway
      if (featureNo === 0) {
        addFeature({
          feature_id: "F01",
          feature_type: "keyway",
          positioning_mode: "segment_relative",
          segment_index: 2,
          segment_offset_mm: 15,
          keyway_width_mm: 8,
          keyway_depth_mm: 4,
          feature_length_mm: 30
        });
      }
      // Material/blank/heat-treatment are assigned programmatically; refresh the preview once all fields are in place
      debouncePreview();

    } catch (error) {
      console.error('Failed to load case:', error);
      // Load default if case loading fails
      addSegment({segment_id:"S01",diameter_mm:60,length_mm:180});
    }
  }

  // Clear case and reload page without case parameter
  window.clearCase = function() {
    window.location.href = '/custom';
  };

  // ============ Save Case Functionality ============
  let taxonomyOptions = [];

  async function loadTaxonomyForDialog() {
    try {
      const response = await fetch('/api/taxonomy');
      const data = await response.json();
      taxonomyOptions = data.nodes || [];

      const select = document.getElementById('case-taxonomy-select');
      if (!select) return;

      // Build tree structure
      const roots = taxonomyOptions.filter(n => !n.parent_id);
      const children = {};
      taxonomyOptions.forEach(n => {
        if (n.parent_id) {
          if (!children[n.parent_id]) children[n.parent_id] = [];
          children[n.parent_id].push(n);
        }
      });

      let html = '<option value="">Select category</option>';
      roots.forEach(root => {
        html += `<option value="${root.id}">${root.name}</option>`;
        if (children[root.id]) {
          children[root.id].forEach(child => {
            html += `<option value="${child.id}">&nbsp;&nbsp;${child.name}</option>`;
          });
        }
      });
      select.innerHTML = html;

      // Try to pre-select taxonomy based on current case or material
      if (window.CASE_ID_FROM_URL) {
        try {
          const caseResp = await fetch(`/api/cases/${window.CASE_ID_FROM_URL}`);
          const caseData = await caseResp.json();
          if (caseData.case && caseData.case.taxonomy_id) {
            select.value = caseData.case.taxonomy_id;
          }
        } catch {}
      }
    } catch (error) {
      console.error('Failed to load taxonomy:', error);
    }
  }

  window.openSaveCaseDialog = function() {
    const dialog = document.getElementById('save-case-dialog');
    if (dialog) {
      dialog.style.display = 'flex';
      loadTaxonomyForDialog();
    }
  };

  window.closeSaveCaseDialog = function() {
    const dialog = document.getElementById('save-case-dialog');
    if (dialog) {
      dialog.style.display = 'none';
      document.getElementById('save-case-error').classList.add('hidden');
    }
  };

  window.saveCase = async function() {
    const nameInput = document.getElementById('case-name-input');
    const taxonomySelect = document.getElementById('case-taxonomy-select');
    const industryInput = document.getElementById('case-industry-input');
    const descInput = document.getElementById('case-desc-input');
    const errorDiv = document.getElementById('save-case-error');

    const partName = nameInput.value.trim();
    if (!partName) {
      errorDiv.textContent = 'Please enter a case name.';
      errorDiv.classList.remove('hidden');
      return;
    }

    const taxonomyId = taxonomySelect.value || 'other';
    const segments = collectSegments();
    const features = collectFeatures();

    // Build main_features list from features
    const FEATURE_NAMES = {
      keyway: 'Keyway', hole: 'Hole', flat: 'Flat', thread: 'Thread',
      knurl: 'Knurl', bearing_seat: 'Bearing Seat', spline: 'Spline',
      taper: 'Taper', groove: 'Groove', seal_area: 'Seal Area',
      gear_teeth: 'Gear Teeth', flange: 'Flange',
    };
    const mainFeatures = features.map(f => FEATURE_NAMES[f.feature_type] || f.feature_type);

    // Remove duplicates
    const uniqueFeatures = [...new Set(mainFeatures)];

    const payload = {
      part_name: partName,
      taxonomy_id: taxonomyId,
      industry: industryInput.value.trim() || 'Custom',
      material: materialSelect.value,
      description: descInput.value.trim() || null,
      main_features: uniqueFeatures,
      process_plan: [],
      segments: segments,
      features: features,
    };

    try {
      const response = await fetch('/api/cases/save-from-form', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to save case');
      }

      const data = await response.json();
      closeSaveCaseDialog();

      // Show success message and redirect to the new case
      alert(`Case "${partName}" saved successfully! Case ID: ${data.case.case_id}`);
      window.location.href = `/cases/${data.case.case_id}`;
    } catch (error) {
      errorDiv.textContent = error.message;
      errorDiv.classList.remove('hidden');
    }
  };

  // Close dialog on overlay click
  document.addEventListener('click', function(e) {
    const dialog = document.getElementById('save-case-dialog');
    if (e.target === dialog) {
      closeSaveCaseDialog();
    }
  });

  // Initialize
  loadMaterials().then(() => {
    loadCaseFromUrl();
  });

  // ============ Real-time Process Route Preview ============
  const STAGE_COLORS = {
    blank: "", datum: "", rough: "", semi_finish: "", finish_before_heat: "",
    feature_before_heat: "feature", feature_after_heat: "feature",
    pre_heat_treatment: "heat", heat_treatment: "heat", datum_recovery: "heat",
    finish: "", precision_finish: "", precision_feature: "feature",
    feature_before_inspection: "feature", surface_treatment: "heat", deburr: "",
    inspection: "final", packaging: "final",
  };
  const STAGE_LABELS = {
    // stage fallback
    blank: "Blanking", datum: "Datum Setup", rough: "Roughing", semi_finish: "Semi-Finish",
    finish_before_heat: "Pre-HT Finish",
    feature_before_heat: "Pre-HT Feature", feature_after_heat: "Post-HT Feature",
    pre_heat_treatment: "Pre-Heat", heat_treatment: "Heat Treatment", datum_recovery: "Datum Recovery",
    finish: "Finishing", precision_finish: "Precision", precision_feature: "Precision Feature",
    feature_before_inspection: "Pre-Inspect", surface_treatment: "Surface Treat", deburr: "Deburr",
    inspection: "Inspection", packaging: "Packaging",
    // operation name override (takes priority)
    "Blanking": "Cutting", "Face Turning": "Facing", "Center Drilling": "Centering",
    "Rough Turning": "Roughing", "Rough Boring": "Rough Bore", "Semi-finish Turning": "Semi-Finish",
    "Normalizing Pre-treatment": "Normalizing", "Heat Treatment": "Thermal Process",
    "Repair Center Holes": "Re-Center", "Finish Turning": "Finishing",
    "Finish Boring": "Finish Bore", "Finish Grind OD": "OD Grinding",
    "Surface Treatment": "Surface Treat", "Chamfer & Deburr": "Deburr", "Final Inspection": "QC Check",
    "Cleaning": "Cleaning", "Packaging": "Packaging",
  };

  let previewTimer = null;
  let previewAbort = null;

  function debouncePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(requestPreview, 500);
  }

  function collectPreviewPayload() {
    const segments = collectSegments();
    const features = collectFeatures();
    if (!segments.length) return null;
    return {
      material: materialSelect.value.trim(),
      blank_type: document.getElementById("blank-type").value,
      blank_diameter_mm: Number(document.getElementById("blank-diameter").value) || 50,
      blank_inner_diameter_mm: numOrNull(document.getElementById("blank-inner-diameter").value),
      segments: segments,
      features: features,
      global_requirements: collectGlobalRequirements(),
    };
  }

  async function requestPreview() {
    const content = document.getElementById("route-preview-content");
    const warningsBox = document.getElementById("route-preview-warnings");
    const dot = document.getElementById("preview-dot");
    if (!content) return;

    const payload = collectPreviewPayload();
    if (!payload) {
      content.innerHTML = '<div class="preview-empty">Add at least one segment</div>';
      warningsBox.innerHTML = "";
      return;
    }

    dot.className = "dot live";

    // Abort previous in-flight request
    if (previewAbort) previewAbort.abort();
    previewAbort = new AbortController();

    try {
      const resp = await fetch("/api/preview-route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: previewAbort.signal,
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(JSON.stringify(data.detail || data));

      renderPreview(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      content.innerHTML = `<div class="preview-empty" style="color:#ef4444">Preview error</div>`;
      warningsBox.innerHTML = "";
    } finally {
      dot.className = "dot";
    }
  }

  function renderPreview(data) {
    const content = document.getElementById("route-preview-content");
    const warningsBox = document.getElementById("route-preview-warnings");
    const route = data.route || [];
    const warnings = data.warnings || [];
    const capability = data.capability || {};
    const resourceSelection = data.resource_selection || {};
    const resourcesByOperation = new Map(
      (resourceSelection.operation_resources || []).map(item => [item.operation_no, item])
    );

    if (!route.length) {
      content.innerHTML = '<div class="preview-empty">No operations generated</div>';
      warningsBox.innerHTML = "";
      return;
    }

    const esc = v => String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
    const statusLabels = {
      satisfied: "Local tool matched",
      not_applicable: "No tool check",
      not_covered: "Tool library not covered",
      not_satisfied: "Not satisfied",
      unknown: "Check material",
    };
    const machine = capability.machine || {};
    const capabilityHtml = capability.overall
      ? `<div class="capability-summary ${capability.critical_ok ? "ok" : "fail"}">
          <strong>${capability.critical_ok ? "✓ Critical local turning capability matched" : "✕ Critical local turning capability not matched"}</strong>
          <span class="machine-name">${esc(machine.message || "No active turning machine matched")}</span>
        </div>`
      : "";

    content.innerHTML = `${capabilityHtml}<ol class="route-preview-list">${route.map(op => {
      const stage = op.stage || "";
      const cls = STAGE_COLORS[stage] || "";
      const label = STAGE_LABELS[op.name] || STAGE_LABELS[stage] || stage;
      const showLabel = op.name !== "Cleaning" && op.name !== "Packaging";
      const resource = resourcesByOperation.get(op.operation_no);
      const status = resource?.verification_status;
      const badgeClass = status ? status.replaceAll("_", "-") : "unknown";
      const statusLabel = resource?.process_category === "Heat Treatment" && status === "satisfied"
        ? "Local thermal equipment matched"
        : resource?.process_category === "Heat Treatment" && status === "not_satisfied"
          ? "Thermal equipment unavailable"
          : resource?.process_category === "Heat Treatment" && status === "not_applicable"
            ? "Thermal resource check skipped"
          : (statusLabels[status] || status);
      const resourceBadge = status
        ? `<span class="resource-badge ${esc(badgeClass)}" title="${esc(resource.note)}">${esc(statusLabel)}</span>`
        : "";
      // Recommended machine/tool for each operation, shown directly below the step
      const deviceBlocks = [];
      (resource?.machine_recommendations || []).forEach(m => {
        deviceBlocks.push(`<span class="dev-chip"><span class="dev-chip-label">Machine</span>${esc(m.designation)}<span class="muted"> (${esc(m.manufacturer)})</span></span>`);
      });
      (resource?.tool_recommendations || []).forEach(t => {
        if (t.cutting_tool_grade) deviceBlocks.push(`<span class="dev-chip"><span class="dev-chip-label">Tool</span>${esc(t.cutting_tool_grade)}</span>`);
      });
      const deviceHtml = deviceBlocks.length ? `<div class="op-devices">${deviceBlocks.join("")}</div>` : "";
      return `<li>
        <span class="op-num ${cls}">${esc(op.operation_no)}</span>
        <div class="op-body">
          <div class="op-name">${esc(op.name)}${resourceBadge}${showLabel ? ` <span style="font-weight:400;font-size:11px;color:#94a3b8">${esc(label)}</span>` : ""}</div>
          <div class="op-desc" title="${esc(op.description)}">${esc(op.description)}</div>
          ${deviceHtml}
        </div>
      </li>`;
    }).join("")}</ol>`;

    if (warnings.length) {
      warningsBox.innerHTML = warnings.map(w => `<div class="warn-item">⚠ ${esc(w)}</div>`).join("");
    } else {
      warningsBox.innerHTML = "";
    }
  }

  // Attach listeners to all form inputs for real-time preview
  form.addEventListener("input", debouncePreview);
  form.addEventListener("change", debouncePreview);

  // Also trigger preview after adding/removing segments or features
  const origAddSegment = addSegment;
  const origAddFeature = addFeature;
  // We can't easily wrap these, so rely on the input/change events from the new fields

  // Initial preview after load
  setTimeout(requestPreview, 800);
})();
