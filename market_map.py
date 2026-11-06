"""首頁背景：點陣世界地圖，黑底上的點像夜間燈光一樣亮起金光，一條金色走勢線緩慢流動，滑鼠經過時點陣化為 0、1 字元。

地圖輪廓是手繪的簡化多邊形（經度, 緯度），只用來決定哪些格點要畫點，不適合作為精確地理資料。
走勢線與亮點都是裝飾，不代表任何個股或實際行情。
"""

import json

import streamlit.components.v1 as components


MAP_HEIGHT = 680

# 內海（黑海、裏海）與陸地重疊，以奇偶規則挖空。
LAND = [
    # 北美洲
    [(-168, 66), (-162, 70), (-156, 71), (-141, 70), (-128, 70), (-115, 68), (-108, 68), (-95, 68), (-94, 72),
     (-85, 70), (-81, 68), (-87, 64), (-93, 59), (-85, 55), (-80, 51), (-79, 55), (-77, 62), (-70, 62), (-65, 59),
     (-60, 54), (-56, 52), (-65, 50), (-60, 46), (-66, 44), (-70, 43), (-74, 40), (-76, 35), (-81, 31), (-80, 25),
     (-82, 27), (-84, 30), (-90, 29), (-97, 27), (-97, 22), (-95, 18), (-91, 19), (-87, 21), (-88, 16), (-83, 15),
     (-83, 10), (-78, 9), (-80, 7), (-85, 10), (-92, 14), (-97, 16), (-105, 20), (-109, 26), (-113, 31), (-110, 23),
     (-115, 29), (-118, 34), (-123, 39), (-124, 46), (-128, 51), (-135, 58), (-145, 60), (-152, 59), (-160, 55),
     (-165, 54), (-158, 58), (-165, 61), (-165, 64)],
    # 格陵蘭與北極群島
    [(-73, 78), (-60, 82), (-30, 83), (-18, 78), (-22, 70), (-40, 65), (-44, 60), (-52, 65), (-55, 70)],
    [(-80, 73), (-70, 71), (-62, 66), (-68, 63), (-78, 65), (-85, 70)],
    [(-118, 72), (-105, 73), (-102, 69), (-115, 69)],
    [(-90, 77), (-70, 82), (-62, 82), (-78, 76)],
    # 加勒比海
    [(-85, 22), (-75, 20), (-78, 23)],
    [(-74, 19), (-68, 18), (-70, 20)],
    # 南美洲
    [(-78, 9), (-72, 12), (-62, 11), (-52, 5), (-50, 0), (-44, -2), (-35, -6), (-35, -9), (-39, -15), (-41, -22),
     (-48, -26), (-53, -33), (-58, -35), (-62, -39), (-65, -43), (-66, -48), (-69, -52), (-68, -55), (-74, -52),
     (-75, -46), (-73, -38), (-71, -30), (-70, -18), (-76, -14), (-81, -5), (-80, 0), (-78, 3), (-77, 7)],
    # 非洲
    [(-6, 36), (3, 37), (10, 37), (11, 33), (20, 32), (25, 32), (33, 31), (34, 27), (37, 21), (39, 15), (43, 12),
     (51, 12), (48, 5), (42, -2), (39, -6), (40, -15), (35, -20), (35, -24), (32, -29), (27, -34), (20, -35),
     (18, -33), (15, -27), (12, -17), (13, -8), (9, -1), (9, 4), (4, 6), (-4, 5), (-8, 4), (-13, 8), (-17, 14),
     (-16, 20), (-13, 27), (-9, 32)],
    [(49, -12), (50, -16), (47, -25), (44, -24), (44, -17)],
    # 歐亞大陸
    [(-9, 37), (-9, 43), (-2, 43), (-1, 46), (-4, 48), (2, 51), (8, 54), (8, 57), (11, 56), (10, 54), (14, 54),
     (20, 54), (24, 59), (30, 60), (22, 60), (21, 64), (24, 66), (18, 62), (17, 58), (13, 55), (11, 59), (5, 59),
     (5, 62), (14, 68), (25, 71), (33, 69), (41, 67), (35, 65), (45, 68), (60, 69), (68, 69), (73, 72), (80, 73),
     (105, 77), (113, 74), (130, 71), (140, 73), (160, 70), (178, 69), (180, 65), (170, 60), (163, 60), (162, 56),
     (157, 51), (156, 57), (160, 61), (150, 59), (140, 55), (135, 54), (140, 48), (132, 43), (128, 40), (129, 36),
     (126, 34), (125, 39), (121, 40), (118, 38), (122, 37), (120, 34), (122, 30), (119, 25), (114, 22), (109, 21),
     (106, 19), (109, 13), (105, 9), (100, 13), (103, 2), (101, 3), (98, 8), (97, 16), (94, 18), (91, 22), (87, 21),
     (80, 15), (80, 10), (77, 8), (73, 17), (72, 21), (67, 24), (61, 25), (57, 26), (51, 28), (48, 30), (51, 25),
     (56, 25), (59, 23), (55, 17), (45, 13), (43, 13), (39, 21), (35, 28), (34, 31), (36, 36), (30, 36), (27, 37),
     (26, 40), (23, 40), (23, 37), (20, 40), (19, 42), (14, 45), (12, 44), (18, 40), (16, 38), (12, 42), (8, 44),
     (3, 43), (0, 39), (-2, 37), (-5, 36)],
    [(28, 41), (33, 42), (41, 41), (38, 45), (33, 46), (30, 45)],
    [(47, 45), (53, 46), (54, 38), (50, 37), (49, 41)],
    # 歐洲與北極島嶼
    [(-5, 50), (1, 51), (0, 54), (-3, 58), (-6, 57), (-3, 54)],
    [(-10, 52), (-6, 52), (-6, 55), (-9, 54)],
    [(-24, 65), (-14, 66), (-14, 64), (-22, 63)],
    [(52, 71), (68, 77), (57, 71)],
    [(12, 78), (25, 80), (20, 77)],
    # 東亞與東南亞島嶼
    [(130, 32), (132, 34), (136, 35), (141, 36), (141, 41), (139, 38), (134, 36), (130, 34)],
    [(140, 42), (145, 43), (142, 45)],
    [(142, 54), (143, 47), (142, 46)],
    [(120, 22), (120, 26), (122, 26), (122, 22)],
    [(120, 18), (122, 18), (124, 12), (126, 7), (122, 7), (120, 14)],
    [(80, 10), (82, 7), (80, 6)],
    [(95, 5), (104, -3), (106, -6), (100, -2)],
    [(106, -6), (114, -7), (114, -9), (106, -8)],
    [(109, 1), (117, 7), (119, 1), (115, -4), (110, -3)],
    [(120, 1), (125, 1), (122, -5), (119, -5)],
    [(131, -1), (141, -3), (150, -10), (141, -9), (134, -4)],
    # 大洋洲
    [(114, -22), (122, -18), (130, -12), (136, -12), (136, -15), (140, -17), (142, -11), (146, -19), (153, -26),
     (150, -37), (144, -39), (138, -35), (132, -32), (124, -33), (115, -34)],
    [(145, -41), (148, -41), (147, -44)],
    [(173, -35), (178, -38), (175, -41), (171, -46), (167, -46), (172, -41)],
]

MAP_DOCUMENT = """<!doctype html>
<meta charset="utf-8">
<style>
  html, body { margin: 0; height: 100%; background: transparent; overflow: hidden; }
  canvas { display: block; width: 100%; height: 100%; }
</style>
<canvas aria-hidden="true"></canvas>
<script>
const LAND = __LAND__;
const GOLD = "#f2c14e", DOT = "#42464f";
const GLYPH_FONT = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
const GRID = 2;
const REACH = 58;          // 滑鼠影響半徑（px）
const COOL_SECONDS = 1.2;  // 滑鼠留下的痕跡冷卻時間
const GLOW_SECONDS = 1.5;  // 點亮後熄滅的時間

const canvas = document.querySelector("canvas");
const ctx = canvas.getContext("2d");
const staticLayer = document.createElement("canvas");
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
const random = (low, high) => low + Math.random() * (high - low);

function onLand(lon, lat) {
  let inside = false;
  for (const polygon of LAND) {
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
      if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

const grid = [];
for (let lat = 82; lat >= -56; lat -= GRID) {
  for (let lon = -180; lon < 180; lon += GRID) {
    if (onLand(lon, lat)) grid.push([lon, lat]);
  }
}
const count = grid.length;
const dotX = new Float32Array(count), dotY = new Float32Array(count);
const heat = new Float32Array(count), glow = new Float32Array(count);
const pullX = new Float32Array(count), pullY = new Float32Array(count);

let W = 0, H = 0, spacing = 8, radius = 1.5, visible = [];

function layout() {
  W = innerWidth; H = innerHeight;
  const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = staticLayer.width = W * ratio;
  canvas.height = staticLayer.height = H * ratio;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  // 窄畫面放不下全球，改以亞洲為中心。
  const narrow = W < 1000;
  const scale = narrow ? Math.max(W / (W < 500 ? 125 : 200), H / 230) : Math.max(W / 340, H / 135);
  const originX = W / 2 - (narrow ? 100 : 10) * scale;
  const originY = H * .5 + 18 * scale;
  spacing = GRID * scale;
  radius = Math.min(Math.max(scale * .34, 1), 2.1);
  visible = [];
  const layer = staticLayer.getContext("2d");
  layer.setTransform(ratio, 0, 0, ratio, 0, 0);
  layer.clearRect(0, 0, W, H);
  layer.fillStyle = DOT;
  for (let i = 0; i < count; i++) {
    dotX[i] = originX + grid[i][0] * scale;
    dotY[i] = originY - grid[i][1] * scale;
    heat[i] = glow[i] = pullX[i] = pullY[i] = 0;
    if (dotX[i] < -spacing || dotX[i] > W + spacing || dotY[i] < -spacing || dotY[i] > H + spacing) continue;
    visible.push(i);
    layer.beginPath();
    layer.arc(dotX[i], dotY[i], radius, 0, 7);
    layer.fill();
  }
}

/* 一條緩慢流動的金色走勢線，線下只留一點透明色。 */
function drawWave(now) {
  const t = now / 1000;
  const base = H * .64;
  const points = [];
  for (let x = 0; x <= W + 8; x += 8) {
    const u = x / W;
    points.push([x, base - H * (
      .13 * Math.sin(u * 5 + t * .3) + .06 * Math.sin(u * 13 + t * .5) + .025 * Math.sin(u * 31 - t * .4) + .1 * (u - .5)
    )]);
  }
  ctx.fillStyle = ctx.strokeStyle = GOLD;
  ctx.lineJoin = ctx.lineCap = "round";
  ctx.globalAlpha = .022;
  for (const depth of [12, 26, 44]) {
    ctx.beginPath();
    points.forEach(([x, y], n) => (n ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    for (let n = points.length - 1; n >= 0; n--) ctx.lineTo(points[n][0], points[n][1] + depth);
    ctx.closePath();
    ctx.fill();
  }
  ctx.globalAlpha = .38;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  points.forEach(([x, y], n) => (n ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/* 滑鼠留下的痕跡：靠近的點先變粗，最靠近的變成 0、1 字元，離開後再冷卻回點。 */
let pointer = null, rings = [];

function segmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length)) : 0;
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}

function wake(x, y) {
  const from = pointer || { x, y };
  const moveX = x - from.x, moveY = y - from.y;
  for (const i of visible) {
    const distance = segmentDistance(dotX[i], dotY[i], from.x, from.y, x, y);
    if (distance >= REACH) continue;
    const strength = 1 - distance / REACH;
    if (strength > heat[i]) heat[i] = strength;
    pullX[i] += (Math.max(-14, Math.min(14, moveX * .5)) * strength - pullX[i]) * .35;
    pullY[i] += (Math.max(-14, Math.min(14, moveY * .5)) * strength - pullY[i]) * .35;
  }
  pointer = { x, y };
}

function drawDots(now, seconds) {
  const glyphSize = Math.max(spacing * 1.2, 8);
  const eraseSize = spacing * .9;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `600 ${glyphSize}px ${GLYPH_FONT}`;
  const flicker = Math.floor(now / 110);
  for (const i of visible) {
    if (glow[i] > 0) glow[i] = Math.max(0, glow[i] - seconds / GLOW_SECONDS);
    if (heat[i] > 0) heat[i] = Math.max(0, heat[i] - seconds / COOL_SECONDS);
    const lit = glow[i], hot = heat[i];
    if (lit <= 0 && hot <= 0) continue;
    const x = dotX[i] + pullX[i] * hot, y = dotY[i] + pullY[i] * hot;
    if (hot > 0) {
      ctx.clearRect(dotX[i] - eraseSize / 2, dotY[i] - eraseSize / 2, eraseSize, eraseSize);
      ctx.fillStyle = GOLD;
      if (hot > .4) {
        const glyphs = hot > .62 ? "01" : "01:";
        ctx.fillText(glyphs[(i * 7 + flicker * (i % 3 + 1)) % glyphs.length], x, y);
      } else {
        ctx.beginPath();
        ctx.arc(x, y, radius * (1 + hot * 1.7), 0, 7);
        ctx.fill();
      }
      continue;
    }
    ctx.fillStyle = GOLD;
    ctx.globalAlpha = lit * .16;
    ctx.beginPath(); ctx.arc(x, y, radius * 4.5, 0, 7); ctx.fill();
    ctx.globalAlpha = Math.min(1, lit * 1.5);
    ctx.beginPath(); ctx.arc(x, y, radius * (1 + lit * .55), 0, 7); ctx.fill();
    ctx.globalAlpha = 1;
  }
}

let last = 0;

function step(now) {
  const seconds = Math.min((now - last) / 1000, .05);
  last = now;
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(staticLayer, 0, 0, W, H);

  // 隨機亮起的點。
  const sparks = (visible.length / 34) * seconds;
  for (let n = Math.floor(sparks) + (Math.random() < sparks % 1 ? 1 : 0); n > 0; n--) {
    const i = visible[Math.floor(Math.random() * visible.length)];
    glow[i] = random(.55, 1);
  }

  // 按下滑鼠送出的環狀波。
  rings = rings.filter((ring) => (ring.radius += seconds * 380) < 420);
  for (const ring of rings) {
    for (const i of visible) {
      const distance = Math.abs(Math.hypot(dotX[i] - ring.x, dotY[i] - ring.y) - ring.radius);
      if (distance < 16) heat[i] = Math.max(heat[i], (1 - distance / 16) * (1 - ring.radius / 420) * 1.1);
    }
  }

  drawWave(now);
  drawDots(now, seconds);
  requestAnimationFrame(step);
}

function refresh() {
  layout();
  rings = []; pointer = null;
  if (calm) ctx.drawImage(staticLayer, 0, 0, W, H);
}

// iframe 鋪在內容後方、不接收滑鼠，所以到外層頁面監聽，再換算成畫布座標。
function bindPointer() {
  const host = window.parent.document;
  const local = (event) => {
    const box = window.frameElement.getBoundingClientRect();
    const x = event.clientX - box.left, y = event.clientY - box.top;
    return x >= 0 && y >= 0 && x <= box.width && y <= box.height ? [x, y] : null;
  };
  const move = (event) => {
    const point = local(event);
    if (point) wake(point[0], point[1]);
    else pointer = null;
  };
  const press = (event) => {
    const point = local(event);
    if (point) rings.push({ x: point[0], y: point[1], radius: 0 });
  };
  host.addEventListener("pointermove", move, { passive: true });
  host.addEventListener("pointerdown", press, { passive: true });
  addEventListener("pagehide", () => {
    host.removeEventListener("pointermove", move);
    host.removeEventListener("pointerdown", press);
  });
}

addEventListener("resize", refresh);
refresh();
if (!calm) {
  bindPointer();
  requestAnimationFrame((now) => { last = now; step(now); });
}
</script>
"""


def render_market_map() -> None:
    """以 iframe 顯示動畫地圖；外層容器的 CSS 負責把它鋪滿 hero 背景。"""
    document = MAP_DOCUMENT.replace("__LAND__", json.dumps(LAND, separators=(",", ":")))
    components.html(document, height=MAP_HEIGHT)
