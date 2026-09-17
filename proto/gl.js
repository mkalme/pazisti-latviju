/* Real-time WebGL2 street renderer — prototype.
 *
 * Every frame is drawn from scratch on the GPU: no cached bitmaps, no
 * settle timers, sharp at any scale mid-gesture. The whole network (named
 * streets + unnamed ctx roads) is ONE static instanced buffer — each road
 * segment is an instance expanded in the vertex shader to a screen-space
 * quad and shaded as an SDF capsule (round caps double as joins, ~1.25 px
 * feathered edge as antialiasing). Per-street color lives in a 2048x1 RGBA
 * texture indexed by a per-instance feature id, so recoloring (hover,
 * quiz answers, theme switch) is a texel upload, never a rebuild.
 *
 * Cartographic style: streets render as CASING + FILL, layered by class.
 * Within a class all casings draw before all fills, so equal streets merge
 * into a continuous surface at junctions; each higher class draws entirely
 * on top, so a main road's border goes over the side streets it crosses.
 * Width is class-graded with per-class pixel clamps: majors stay visibly
 * thicker than side streets at every zoom.
 *
 * Pan/zoom (js/view.js) and picking (js/spatial.js) are the game's own
 * modules, unchanged.
 */
(function () {
  "use strict";
  var data = window.RIGA_DATA;
  var view = App.view;

  // Width per class: world meters, clamped to a per-class pixel range so
  // the hierarchy survives both far and near zooms.
  // fadeTo: how far a class fades toward the background when zoomed out
  // (its true width shrinking under the pixel floor). Majors never fade —
  // zoomed out, the arteries stay full-strength over a faint minor-road
  // carpet, which is what makes them readable as MAIN at city scale.
  var W_STREET = [
    { base: 14.0, min: 2.8, max: 26.0, fadeTo: 1.0 },   // 0 highways (few)
    { base: 10.0, min: 2.2, max: 20.0, fadeTo: 1.0 },   // 1 primary
    { base: 7.5,  min: 1.6, max: 15.0, fadeTo: 0.65 },  // 2 secondary
    { base: 5.5,  min: 1.2, max: 12.0, fadeTo: 0.42 },  // 3 tertiary
    { base: 4.0,  min: 1.0, max: 10.0, fadeTo: 0.28 }   // 4 residential
  ];
  var W_CTX = [
    { base: 10.0, min: 0.7, max: 9.0, fadeTo: 0.22 },
    { base: 7.0,  min: 0.7, max: 8.0, fadeTo: 0.22 },
    { base: 5.0,  min: 0.7, max: 7.0, fadeTo: 0.22 },
    { base: 4.0,  min: 0.7, max: 6.0, fadeTo: 0.22 },
    { base: 3.0,  min: 0.7, max: 5.0, fadeTo: 0.22 }
  ];
  var BORDER_PX = 1.6;    // casing thickness per side, css px
  // The cased style fades in ABOVE every class's minimum width, so a
  // zoomed-out map is clean ink lines (majors merely thicker) and never
  // sits half-faded — pale washed ribbons at overview looked wrong.
  var CASE_LO = 3.2;      // px: below this a street is a plain ink line
  var CASE_HI = 4.8;      // px: above this it is fully casing + fill
  var TEXW = 2048;        // state texture width (1860 feats used)

  // Four style families, each with a light and a dark variant. `ink` and
  // `street` are per class (majors, secondary, residential); `ink` is the
  // zoomed-out line color, `street` the cased fill.
  var STYLES = {
    paper: {   // warm paper — hue on the top two tiers, stepped neutrals below
      light: {
        bg: [244, 241, 234], ctx: "#dcd7cc", casing: "#b3ab9b",
        ink: ["#c05f21", "#c69426", "#8a8577", "#a29a8b", "#b9b1a2"],
        street: ["#f4a45a", "#f9d876", "#ffffff", "#f6f1e6", "#eee8db"],
        hover: "#2563eb", flash: "#1fa94e"   // blue — never reads as a road tier
      },
      dark: {
        bg: [21, 23, 26], ctx: "#24282e", casing: "#3d444d",
        ink: ["#d08a3c", "#b89b45", "#79818c", "#5d646e", "#4b525b"],
        street: ["#93672f", "#83743c", "#5f6873", "#525a63", "#464d55"],
        hover: "#60a5fa", flash: "#2fbf62"
      }
    },
    osm: {     // classic OpenStreetMap-carto tones
      light: {
        bg: [242, 239, 233], ctx: "#ded9cf", casing: "#a9a498",
        ink: ["#d1543a", "#d29a2c", "#9fa146", "#9a9488", "#b1aca1"],
        street: ["#f9b29c", "#fcd6a4", "#f7fabf", "#ffffff", "#efece4"],
        hover: "#1971c2", flash: "#2f9e44"
      },
      dark: {
        bg: [24, 26, 23], ctx: "#2a2d28", casing: "#45483f",
        ink: ["#d4744e", "#c29638", "#8b8d51", "#6f7268", "#585c56"],
        street: ["#8a5340", "#84683a", "#63673f", "#565a50", "#464a44"],
        hover: "#4dabf7", flash: "#51cf66"
      }
    },
    mono: {    // minimal ink — grayscale hierarchy, accent only on action
      light: {
        bg: [251, 250, 248], ctx: "#e2e1de", casing: "#9a9994",
        ink: ["#1d1c18", "#55534d", "#8b8a84", "#aaa9a3", "#c4c3bd"],
        street: ["#b4b3ac", "#cfcec7", "#e6e5e0", "#f1f0ec", "#f8f7f4"],
        hover: "#e8590c", flash: "#2b8a3e"
      },
      dark: {
        bg: [15, 16, 18], ctx: "#232527", casing: "#56595d",
        ink: ["#eef1f5", "#b4b7bc", "#84878c", "#5f6266", "#45474a"],
        street: ["#5f6368", "#4d5155", "#3d4044", "#323538", "#292b2e"],
        hover: "#ffa94d", flash: "#40c057"
      }
    },
    slate: {   // cool slate — navigation-app blue-gray
      light: {
        bg: [232, 237, 242], ctx: "#d3dae1", casing: "#90a0af",
        ink: ["#14497e", "#3f74a8", "#728694", "#93a0ad", "#b0bac4"],
        street: ["#9cc3e8", "#dcebf8", "#ffffff", "#eef3f8", "#e4eaf0"],
        hover: "#f76707", flash: "#2b8a3e"
      },
      dark: {
        bg: [13, 21, 32], ctx: "#1b2634", casing: "#3f5872",
        ink: ["#9cc4ec", "#6f9cc9", "#4e6883", "#3d5266", "#324457"],
        street: ["#4e6c96", "#3d567a", "#2d4059", "#25354a", "#1e2c3e"],
        hover: "#ffb454", flash: "#37b24c"
      }
    }
  };
  var styleId = "paper";
  var theme = "light";

  function PAL() {
    return STYLES[styleId][theme];
  }

  var canvas = document.getElementById("map");
  var gl = canvas.getContext("webgl2", { alpha: false, antialias: true });
  if (!gl) {
    document.getElementById("err").style.display = "flex";
    return;
  }

  /* ---------- shaders ---------- */
  // u_pass: 0 ctx · 1 casing · 2 fill · 3 top casing · 4 top fill

  var VS = "#version 300 es\n" +
    "layout(location=0) in vec4 a_seg;\n" +   // ax, ay, bx, by (world m)
    "layout(location=1) in float a_feat;\n" +
    "layout(location=2) in float a_w;\n" +    // base width, world m
    "layout(location=3) in vec2 a_wlim;\n" +  // px clamp (min, max)
    "layout(location=4) in float a_fmin;\n" + // zoom-out fade floor
    "uniform vec3 u_view;\n" +                // scale, tx, ty (CSS px)
    "uniform vec2 u_res;\n" +                 // canvas size, device px
    "uniform float u_dpr;\n" +
    "uniform int u_pass;\n" +
    "uniform float u_top;\n" +                // feature id for passes 3/4
    "uniform float u_widen;\n" +              // extra px (highlight)
    "uniform float u_border;\n" +
    "uniform vec4 u_case;\n" +
    "uniform vec4 u_ink;\n" +
    "uniform vec4 u_bg;\n" +
    "uniform sampler2D u_state;\n" +
    "out vec2 v_p; out vec2 v_a; out vec2 v_b;\n" +
    "out float v_half; out vec4 v_col;\n" +
    "void main() {\n" +
    "  vec4 col = texelFetch(u_state, ivec2(int(a_feat), 0), 0);\n" +
    "  float wraw = a_w * u_view.x;\n" +
    "  float wf = clamp(wraw, a_wlim.x, a_wlim.y);\n" +
    "  bool ctx = a_feat < 0.5;\n" +
    "  bool off = col.a == 0.0;\n" +
    "  float w = wf;\n" +
    // Zoom styling: thin streets draw as plain ink lines; casing + pale
    // fill fade in once the street is wide enough to carry a border.
    // Zoomed out, a class fades toward the background as its true width
    // drops under its pixel floor (mixed opaque — alpha would double-
    // darken at crossings), so majors pop over a faint minor carpet.
    "  float cased = smoothstep(" + CASE_LO + ", " + CASE_HI + ", wf);\n" +
    "  float fade = mix(a_fmin, 1.0, clamp(wraw / a_wlim.x, 0.0, 1.0));\n" +
    "  if (u_pass == 0) { off = off || !ctx;\n" +
    "    col = vec4(mix(u_bg.rgb, col.rgb, fade), col.a); }\n" +
    "  else if (u_pass == 1) { off = off || ctx || cased <= 0.01;\n" +
    "    w = wf + 2.0 * u_border; col = vec4(u_case.rgb, u_case.a * cased); }\n" +
    "  else if (u_pass == 2) { off = off || ctx;\n" +
    "    vec4 inkc = vec4(mix(u_bg.rgb, u_ink.rgb, fade), 1.0);\n" +
    "    col = mix(inkc, col, cased); }\n" +
    "  else if (u_pass == 3) { off = off || abs(a_feat - u_top) > 0.5;\n" +
    "    w = wf + u_widen + 2.0 * u_border; col = u_case; }\n" +
    "  else { off = off || abs(a_feat - u_top) > 0.5; w = wf + u_widen; }\n" +
    "  vec2 A = (a_seg.xy * u_view.x + u_view.yz) * u_dpr;\n" +
    "  vec2 B = (a_seg.zw * u_view.x + u_view.yz) * u_dpr;\n" +
    "  w *= u_dpr;\n" +
    "  float hf = w * 0.5 + 1.25;\n" +
    "  vec2 d = B - A;\n" +
    "  float len = max(length(d), 1e-6);\n" +
    "  d /= len;\n" +
    "  vec2 n = vec2(-d.y, d.x);\n" +
    "  int c = gl_VertexID;\n" +
    "  vec2 pos = (c == 0) ? A - d * hf - n * hf\n" +
    "           : (c == 1) ? A - d * hf + n * hf\n" +
    "           : (c == 2) ? B + d * hf - n * hf\n" +
    "           :            B + d * hf + n * hf;\n" +
    "  if (off) pos = vec2(-1e6);\n" +
    "  v_p = pos; v_a = A; v_b = B; v_half = hf; v_col = col;\n" +
    "  vec2 ndc = pos / u_res * 2.0 - 1.0;\n" +
    "  gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);\n" +
    "}\n";

  var FS = "#version 300 es\n" +
    "precision highp float;\n" +
    "in vec2 v_p; in vec2 v_a; in vec2 v_b;\n" +
    "in float v_half; in vec4 v_col;\n" +
    "out vec4 o;\n" +
    "void main() {\n" +
    "  vec2 pa = v_p - v_a, ba = v_b - v_a;\n" +
    "  float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-6), 0.0, 1.0);\n" +
    "  float dist = length(pa - ba * h);\n" +
    "  float alpha = smoothstep(v_half, v_half - 1.25, dist) * v_col.a;\n" +
    "  if (alpha < 0.004) discard;\n" +
    "  o = vec4(v_col.rgb, alpha);\n" +
    "}\n";

  function compile(type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(s));
    }
    return s;
  }
  var prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(prog));
  }
  var U = {};
  ["u_view", "u_res", "u_dpr", "u_pass", "u_top", "u_widen", "u_border",
   "u_case", "u_ink", "u_bg", "u_state"].forEach(function (n) {
    U[n] = gl.getUniformLocation(prog, n);
  });

  /* ---------- static instance buffer ---------- */

  // The buffer is ordered ctx, then streets by class (2, 1, 0). Each block's
  // instance range is recorded so classes can be drawn as separate layers:
  // a main road's casing AND fill go OVER the side streets it crosses.
  var ranges = { ctx: [0, 0], cls: { 4: [0, 0], 3: [0, 0], 2: [0, 0], 1: [0, 0], 0: [0, 0] } };

  function buildInstances() {
    var out = [];
    function push(seg, feat, spec) {
      for (var i = 0; i < seg.length - 1; i++) {
        out.push(seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1],
                 feat, spec.base, spec.min, spec.max, spec.fadeTo);
      }
    }
    var c, i, st;
    for (c = 4; c >= 0; c--) {          // ctx underneath, majors on top
      for (i = 0; i < data.ctx.length; i++) {
        if (data.ctx[i].c === c) push(data.ctx[i].s, 0, W_CTX[c]);
      }
    }
    ranges.ctx = [0, out.length / 9];
    for (c = 4; c >= 0; c--) {
      var start = out.length / 9;
      for (i = 0; i < data.streets.length; i++) {
        st = data.streets[i];
        if (st.cls !== c) continue;
        for (var g = 0; g < st.segs.length; g++) {
          push(st.segs[g], st.id + 1, W_STREET[c]);
        }
      }
      ranges.cls[c] = [start, out.length / 9 - start];
    }
    return new Float32Array(out);
  }

  var inst = buildInstances();
  var N = inst.length / 9;
  var vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  var vbo = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
  gl.bufferData(gl.ARRAY_BUFFER, inst, gl.STATIC_DRAW);

  // Point the instance attributes at a given first instance — WebGL2 has
  // no baseInstance, so per-class layer draws re-aim the pointers instead.
  function bindFrom(start) {
    var o = start * 36;
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 36, o);
    gl.vertexAttribDivisor(0, 1);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 1, gl.FLOAT, false, 36, o + 16);
    gl.vertexAttribDivisor(1, 1);
    gl.enableVertexAttribArray(2);
    gl.vertexAttribPointer(2, 1, gl.FLOAT, false, 36, o + 20);
    gl.vertexAttribDivisor(2, 1);
    gl.enableVertexAttribArray(3);
    gl.vertexAttribPointer(3, 2, gl.FLOAT, false, 36, o + 24);
    gl.vertexAttribDivisor(3, 1);
    gl.enableVertexAttribArray(4);
    gl.vertexAttribPointer(4, 1, gl.FLOAT, false, 36, o + 32);
    gl.vertexAttribDivisor(4, 1);
  }
  bindFrom(0);

  /* ---------- per-feature state texture ---------- */

  var state = new Uint8Array(TEXW * 4);
  var tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  function hex(h) {
    return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16),
            parseInt(h.slice(5, 7), 16)];
  }
  function setTexel(feat, rgb) {
    state[feat * 4] = rgb[0];
    state[feat * 4 + 1] = rgb[1];
    state[feat * 4 + 2] = rgb[2];
    state[feat * 4 + 3] = 255;
  }
  function baseColor(feat) {
    var P = PAL();
    if (feat === 0) return hex(P.ctx);
    return hex(P.street[data.streets[feat - 1].cls]);
  }
  function uploadTexel(feat) {
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, feat, 0, 1, 1, gl.RGBA,
                     gl.UNSIGNED_BYTE, state.subarray(feat * 4, feat * 4 + 4));
  }
  function fillStates() {
    setTexel(0, baseColor(0));
    for (var i = 0; i < data.streets.length; i++) setTexel(i + 1, baseColor(i + 1));
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, TEXW, 1, 0, gl.RGBA,
                  gl.UNSIGNED_BYTE, state);
  }
  fillStates();

  /* ---------- interaction: hover, click flash ---------- */

  var tip = document.getElementById("tip");
  var hoverFeat = -1;
  var flashes = new Map();   // feat -> timeout id

  // One physical street can be several entities (an A corridor plus the
  // city street it becomes) — the data links them by `grp`, so hover and
  // flash treat the whole group as one street.
  var grpMembers = {};
  data.streets.forEach(function (s) {
    (grpMembers[s.grp] = grpMembers[s.grp] || []).push(s.id + 1);
  });
  function groupOf(feat) {
    return feat > 0 ? grpMembers[data.streets[feat - 1].grp] : [];
  }

  function setHover(feat, cx, cy) {
    if (hoverFeat !== feat) {
      groupOf(hoverFeat).forEach(function (f) {
        if (!flashes.has(f)) { setTexel(f, baseColor(f)); uploadTexel(f); }
      });
      hoverFeat = feat;
      groupOf(feat).forEach(function (f) {
        if (!flashes.has(f)) { setTexel(f, hex(PAL().hover)); uploadTexel(f); }
      });
      canvas.style.cursor = feat > 0 ? "pointer" : "crosshair";
    }
    if (feat > 0) {
      tip.textContent = data.streets[feat - 1].name;
      tip.style.display = "block";
      tip.style.left = (cx + 14) + "px";
      tip.style.top = (cy + 14) + "px";
    } else {
      tip.style.display = "none";
    }
  }

  view.onHover = function (wx, wy, cx, cy) {
    if (wx === null) { setHover(-1); return; }
    var hit = App.spatial.pick(wx, wy, 8 / view.scale);
    setHover(hit ? hit.id + 1 : -1, cx, cy);
  };

  view.onClick = function (wx, wy) {
    var hit = App.spatial.pick(wx, wy, 12 / view.scale);
    if (!hit) return;
    groupOf(hit.id + 1).forEach(function (feat) {
      if (flashes.has(feat)) clearTimeout(flashes.get(feat));
      setTexel(feat, hex(PAL().flash));
      uploadTexel(feat);
      flashes.set(feat, setTimeout(function () {
        flashes.delete(feat);
        var hov = groupOf(hoverFeat).indexOf(feat) >= 0;
        setTexel(feat, hov ? hex(PAL().hover) : baseColor(feat));
        uploadTexel(feat);
      }, 450));
    });
  };

  /* ---------- legend: swatches from the LIVE palette ---------- */

  var TIER_LABELS = ["A ceļi", "Maģistrāles", "Lielas ielas",
                     "Vidējas ielas", "Mazas ielas"];
  var LEG_INK_W = [3.2, 2.6, 2.1, 1.7, 1.4];
  var LEG_PILL_H = [13, 11, 9.5, 8.5, 7.5];

  function legendSwatch(cnv, inkColor, fillColor, withCasing, inkW, pillH) {
    var dpr = window.devicePixelRatio || 1;
    cnv.width = Math.round(92 * dpr);
    cnv.height = Math.round(18 * dpr);
    cnv.style.width = "92px";
    cnv.style.height = "18px";
    var c = cnv.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, 92, 18);
    c.lineCap = "round";
    // left: the zoomed-out ink line · right: the zoomed-in cased pill
    c.strokeStyle = inkColor;
    c.lineWidth = inkW;
    c.beginPath(); c.moveTo(3, 9); c.lineTo(30, 9); c.stroke();
    if (withCasing) {
      c.strokeStyle = PAL().casing;
      c.lineWidth = pillH;
      c.beginPath(); c.moveTo(44, 9); c.lineTo(86, 9); c.stroke();
    }
    c.strokeStyle = fillColor;
    c.lineWidth = withCasing ? pillH - 2 * BORDER_PX : pillH;
    c.beginPath(); c.moveTo(44, 9); c.lineTo(86, 9); c.stroke();
  }

  function renderLegend() {
    var box = document.getElementById("legend");
    var P = PAL();
    if (!box.firstChild) {
      var head = document.createElement("div");
      head.className = "lhead";
      head.innerHTML = "<span><i>tālu</i><i>tuvu</i></span><span></span>";
      box.appendChild(head);
      for (var i = 0; i < 7; i++) {
        var row = document.createElement("div");
        row.className = "lrow";
        var cnv = document.createElement("canvas");
        var lab = document.createElement("span");
        row.appendChild(cnv);
        row.appendChild(lab);
        box.appendChild(row);
      }
    }
    var rows = box.querySelectorAll(".lrow");
    for (var t = 0; t < 5; t++) {
      legendSwatch(rows[t].firstChild, P.ink[t], P.street[t], true,
                   LEG_INK_W[t], LEG_PILL_H[t]);
      rows[t].lastChild.textContent = TIER_LABELS[t];
    }
    legendSwatch(rows[5].firstChild, P.ctx, P.ctx, false, 1.2, 5);
    rows[5].lastChild.textContent = "Bezvārda ceļi";
    legendSwatch(rows[6].firstChild, P.hover, P.hover, true, 2.6, 10);
    rows[6].lastChild.textContent = "Meklētā iela";
  }

  /* ---------- HUD ---------- */

  var hFps = document.getElementById("h-fps");
  var hMs = document.getElementById("h-ms");
  var hScale = document.getElementById("h-scale");
  document.getElementById("h-inst").textContent = N.toLocaleString("lv");

  document.getElementById("b-reset").addEventListener("click", function () {
    view.fitBbox(data.meta.bounds);
  });
  document.getElementById("b-theme").addEventListener("click", function (e) {
    theme = theme === "light" ? "dark" : "light";
    document.body.classList.toggle("dark", theme === "dark");
    e.target.textContent = theme === "light" ? "Tumšais" : "Gaišais";
    fillStates();   // whole palette re-resolves — one 8 KB upload
    renderLegend();
    if (hoverFeat > 0) { setTexel(hoverFeat, hex(PAL().hover)); uploadTexel(hoverFeat); }
  });
  document.getElementById("s-style").addEventListener("change", function (e) {
    styleId = e.target.value;
    fillStates();
    renderLegend();
    if (hoverFeat > 0) { setTexel(hoverFeat, hex(PAL().hover)); uploadTexel(hoverFeat); }
  });

  /* ---------- frame loop: full redraw, every frame ---------- */

  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA,
                       gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

  var lastT = 0, emaMs = 0, lastHud = 0;

  function frame(now) {
    if (lastT) {
      var dt = now - lastT;
      emaMs = emaMs ? emaMs * 0.9 + dt * 0.1 : dt;
    }
    lastT = now;
    if (now - lastHud > 250) {
      lastHud = now;
      hFps.textContent = emaMs ? (1000 / emaMs).toFixed(0) : "–";
      hMs.textContent = emaMs.toFixed(1) + " ms";
      hScale.textContent = view.scale.toFixed(2) + " px/m";
    }

    gl.viewport(0, 0, canvas.width, canvas.height);
    var P = PAL();
    gl.clearColor(P.bg[0] / 255, P.bg[1] / 255, P.bg[2] / 255, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);

    gl.useProgram(prog);
    gl.bindVertexArray(vao);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.uniform1i(U.u_state, 0);
    gl.uniform3f(U.u_view, view.scale, view.tx, view.ty);
    gl.uniform2f(U.u_res, canvas.width, canvas.height);
    gl.uniform1f(U.u_dpr, window.devicePixelRatio || 1);
    gl.uniform1f(U.u_top, -1);
    gl.uniform1f(U.u_widen, 0);
    gl.uniform1f(U.u_border, BORDER_PX);
    var cs = hex(P.casing);
    gl.uniform4f(U.u_case, cs[0] / 255, cs[1] / 255, cs[2] / 255, 1);
    gl.uniform4f(U.u_bg, P.bg[0] / 255, P.bg[1] / 255, P.bg[2] / 255, 1);

    // Layered by class: ctx roads, then per class (minor to major) casing
    // followed by fill. Within a class the fills overlap the casings so
    // equal streets merge at junctions; a HIGHER class draws entirely on
    // top, so a main road's border goes over the side streets it crosses.
    gl.uniform1i(U.u_pass, 0);
    bindFrom(ranges.ctx[0]);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, ranges.ctx[1]);
    for (var cl = 4; cl >= 0; cl--) {
      var r = ranges.cls[cl];
      if (!r[1]) continue;
      bindFrom(r[0]);
      var ink = hex(P.ink[cl]);   // per-class ink — mains a warm tone
      gl.uniform4f(U.u_ink, ink[0] / 255, ink[1] / 255, ink[2] / 255, 1);
      gl.uniform1i(U.u_pass, 1);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, r[1]);
      gl.uniform1i(U.u_pass, 2);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, r[1]);
    }
    bindFrom(0);

    // Highlight passes: hovered / flashing street redrawn on top, slightly
    // widened, casing and fill — a texel upload plus two extra draw calls
    // is the whole cost of a quiz recolor.
    var tops = [];
    groupOf(hoverFeat).forEach(function (f) { tops.push(f); });
    flashes.forEach(function (_, f) { if (tops.indexOf(f) < 0) tops.push(f); });
    for (var i = 0; i < tops.length && i < 6; i++) {
      gl.uniform1f(U.u_top, tops[i]);
      gl.uniform1f(U.u_widen, 2.5);
      gl.uniform1i(U.u_pass, 3);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, N);
      gl.uniform1i(U.u_pass, 4);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, N);
    }

    requestAnimationFrame(frame);
  }

  /* ---------- boot ---------- */

  App.spatial.build(data.streets);
  view.init(canvas, data.meta.bounds);
  view.fitBbox(data.meta.bounds);
  renderLegend();
  requestAnimationFrame(frame);
})();
