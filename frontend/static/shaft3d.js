/**
 * shaft3d.js — 3D stepped shaft model (Three.js local ES module)
 */
// 文件职责：用 Three.js 在 #shaft-3d-container 中绘制阶梯轴 3D 模型。
// app.js 在结果渲染后动态 import 本模块，暴露全局 renderShaft3D 并以事件通知就绪：
//   - 坐标换算：把整轴总长映射到 3 个世界单位(scale = 3 / total_length_mm)，毫米坐标统一换算；
//   - 各轴段用圆柱体按顺序排布，高精度段(high_precision)以橙色材质区分，段间加端面圆环分界；
//   - 毛坯(blank_diameter_mm)用半透明大圆柱包络整轴；条件特征(键槽/孔/铣面/螺纹/滚花等)
//     用彩色圆环标记并附文字精灵(segment_id / φ径×长、feature_id)；
//   - 提供轨道旋转控制、视口自适应，以及页面隐藏时暂停渲染循环以节省 GPU。
import * as THREE from "./three.module.min.js";
import { OrbitControls } from "./OrbitControls.js";

// 渲染入口：按容器 id 与几何数据(segments / features / total_length_mm / blank_diameter_mm)
// 构建场景。容器不存在或几何缺少轴段时直接返回；每次调用会先清空容器以支持重复渲染。
function renderShaft3D(containerId, geometry) {
  var container = document.getElementById(containerId);
  if (!container || !geometry || !geometry.segments || !geometry.segments.length) return;
  container.innerHTML = "";

  var segments = geometry.segments;
  var features = geometry.features || [];
  var totalLen = geometry.total_length_mm;
  var blankDia = geometry.blank_diameter_mm;

  var width = container.clientWidth || 500;
  var height = 360;

  // ── Scene ──
  var scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf8f9fb);

  var camera = new THREE.PerspectiveCamera(30, width / height, 0.1, 2000);
  var renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.appendChild(renderer.domElement);

  // ── Materials ──
  var matNormal = new THREE.MeshStandardMaterial({ color: 0x8899aa, metalness: 0.6, roughness: 0.35 });
  var matPrecision = new THREE.MeshStandardMaterial({ color: 0xe68a00, metalness: 0.7, roughness: 0.25 });
  var matBlank = new THREE.MeshStandardMaterial({ color: 0xbbc5d0, transparent: true, opacity: 0.12, side: THREE.DoubleSide });
  var featureMats = {
    keyway: new THREE.MeshBasicMaterial({ color: 0x2458d3 }),
    hole: new THREE.MeshBasicMaterial({ color: 0x16794a }),
    flat: new THREE.MeshBasicMaterial({ color: 0x0ea5e9 }),
    thread: new THREE.MeshBasicMaterial({ color: 0xb42318 }),
    knurl: new THREE.MeshBasicMaterial({ color: 0x7c3aed }),
  };

  // ── Scaling: map total length to 3 units ──
  var scale = 3 / totalLen;

  // ── Shaft segment group ──
  var shaftGroup = new THREE.Group();

  segments.forEach(function (seg) {
    var r = (seg.diameter_mm / 2) * scale;
    var h = seg.length_mm * scale;
    var geo = new THREE.CylinderGeometry(r, r, h, 64);
    var mat = seg.high_precision ? matPrecision : matNormal;
    var mesh = new THREE.Mesh(geo, mat);
    // 轴段在整轴上占据的毫米区间换算为世界坐标：以该段中点的全局位置定位，
    // 并整体下移半轴长，使模型垂直居中于原点(圆柱轴向即 Y 轴)。
    var yCenter = ((seg.global_start_mm + seg.global_end_mm) / 2) * scale - (totalLen * scale) / 2;
    mesh.position.y = yCenter;
    shaftGroup.add(mesh);

    // End-face ring between segments (visual separator)
    if (seg.global_start_mm > 0) {
      var ringGeo = new THREE.RingGeometry(r * 0.98, r * 1.01, 64);
      var ringMat = new THREE.MeshBasicMaterial({ color: 0x556677, side: THREE.DoubleSide });
      var ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.y = yCenter - h / 2;
      ring.rotation.x = Math.PI / 2;
      shaftGroup.add(ring);
    }
  });

  // ── Bar stock envelope ──
  var blankR = (blankDia / 2) * scale;
  var blankH = totalLen * scale;
  var blankGeo = new THREE.CylinderGeometry(blankR, blankR, blankH, 64, 1, true);
  var blankMesh = new THREE.Mesh(blankGeo, matBlank);
  shaftGroup.add(blankMesh);

  // ── Feature markers ──
  features.forEach(function (f) {
    // 特征标记定位：按 global_position_mm(整轴左端起)遍历轴段确定其所在段，
    // 以该段半径作为标记环的基准半径(未命中任何段时 segR 保持 0)。
    var y = f.global_position_mm * scale - (totalLen * scale) / 2;
    var segR = 0;
    for (var i = 0; i < segments.length; i++) {
      var s = segments[i];
      if (f.global_position_mm >= s.global_start_mm && f.global_position_mm <= s.global_end_mm) {
        segR = (s.diameter_mm / 2) * scale;
        break;
      }
    }
    // Marker ring
    var ringR = segR + 0.03;
    var ringGeo = new THREE.TorusGeometry(ringR, 0.015, 8, 64);
    var ringMat = featureMats[f.feature_type] || featureMats.keyway;
    var ring = new THREE.Mesh(ringGeo, ringMat);
    ring.position.y = y;
    ring.rotation.x = Math.PI / 2;
    shaftGroup.add(ring);

    // Label sprite
    var label = makeSprite(f.feature_id, ringMat.color.getHex());
    label.position.set(ringR + 0.2, y, 0);
    label.scale.set(0.4, 0.18, 1);
    shaftGroup.add(label);
  });

  scene.add(shaftGroup);

  // ── Lighting ──
  scene.add(new THREE.AmbientLight(0xffffff, 0.5));
  var dir1 = new THREE.DirectionalLight(0xffffff, 0.9);
  dir1.position.set(4, 6, 5);
  scene.add(dir1);
  var dir2 = new THREE.DirectionalLight(0x8899bb, 0.3);
  dir2.position.set(-3, 2, -4);
  scene.add(dir2);

  // ── Axis line ──
  var axisLen = totalLen * scale * 0.55;
  var axisGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, -axisLen, 0),
    new THREE.Vector3(0, axisLen, 0),
  ]);
  scene.add(new THREE.Line(axisGeo, new THREE.LineBasicMaterial({ color: 0xaaaaaa })));

  // ── Segment label sprites ──
  segments.forEach(function (seg) {
    var y1 = seg.global_start_mm * scale - (totalLen * scale) / 2;
    var y2 = seg.global_end_mm * scale - (totalLen * scale) / 2;
    var r = (seg.diameter_mm / 2) * scale;
    var label = makeSprite(seg.segment_id + "  φ" + seg.diameter_mm + "×" + seg.length_mm, 0x475467);
    label.position.set(-r - 0.35, (y1 + y2) / 2, 0);
    label.scale.set(0.5, 0.14, 1);
    shaftGroup.add(label);
  });

  // ── Camera and controls ──
  camera.position.set(3.5, 1, 3.5);
  camera.lookAt(0, 0, 0);
  var controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(0, 0, 0);

  // ── Render loop (paused while the tab is hidden to stop burning GPU) ──
  var rafId = null;
  var running = true;
  // 运行标志开启时更新控制器并绘制下一帧；页面可见性事件负责暂停和恢复。
  function loop() {
    if (!running) return;
    rafId = requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  }
  loop();
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      running = false;
      if (rafId) cancelAnimationFrame(rafId);
    } else if (!running) {
      running = true;
      loop();
    }
  });

  // ── Responsive ──
  window.addEventListener("resize", function () {
    var w = container.clientWidth;
    camera.aspect = w / height;
    camera.updateProjectionMatrix();
    renderer.setSize(w, height);
  });
}

/** Create a text sprite */
// 用画布生成尺寸文字贴图，创建面向相机的标注精灵。
function makeSprite(text, color) {
  var canvas = document.createElement("canvas");
  var ctx = canvas.getContext("2d");
  canvas.width = 512;
  canvas.height = 96;
  ctx.clearRect(0, 0, 512, 96);
  ctx.font = "bold 36px Microsoft YaHei, PingFang SC, system-ui, sans-serif";
  ctx.fillStyle = "#" + new THREE.Color(color).getHexString();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, 256, 48);
  var texture = new THREE.CanvasTexture(canvas);
  return new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }));
}

// Expose to global scope
window.renderShaft3D = renderShaft3D;
window.dispatchEvent(new Event("shaft3d-ready"));
