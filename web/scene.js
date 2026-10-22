/* Agentorium — isometric 2D office drawn on a canvas with smooth vector shapes.
 *
 * Public API (window.Scene):
 *   setRoster(agents)              agents: [{id, name, color, hair, role}] — rebuilds the office layout
 *   setStatus(agentId, text)       '' / 'idle' = relax in the lounge; anything else = work at the desk
 *   send(fromId, toId, label)      envelope flying between two agents ('user' = the door)
 *
 * The office has a work zone (one desk per agent along the back wall) and a lounge
 * (sofa + TV, beds, coffee bar, water cooler). Idle agents wander between lounge activities;
 * when a status arrives they walk to their desk and type until idle again.
 */
window.Scene = (() => {
  const HW = 16, HH = 8;                // half tile width / height (2:1 isometric)
  const WALL_H = 46, PAD = 12, IDLE_GRACE_MS = 2500, SPEED = 0.045;
  const canvas = document.getElementById('scene');
  const ctx = canvas.getContext('2d');

  const PAL = {
    floorA: '#2b2536', floorB: '#2f2a3c', floorLine: 'rgba(0,0,0,.25)', rug: '#3b2f55', rugEdge: '#55447a',
    wallTop: '#2a2e42', wallBottom: '#1c1f2d', wallEdge: '#3a3f55', baseboard: '#15171f',
    skin: '#f3c9a3', eye: '#1b1b1b', mouth: '#b5563b', pants: '#2f3b73', shoe: '#1a1a1a',
    desk: '#7a5533', chair: '#3d4360', monitor: '#15161c', screenOff: '#1d2440', screenOn: '#7df0c0', key: '#9aa0b3',
    bedFrame: '#5a3a1e', mattress: '#d5dae6', pillow: '#f7f8fc', blanket: '#c0505f',
    sofa: '#4f76b0', tv: '#101117', counter: '#6b4a2b', machine: '#3a3f55', cooler: '#cfe6f5', coolerWater: '#6cc3ff',
    plantPot: '#8c4b2a', plant: '#3fa160', lamp: '#f0c66a', night: '#0b1024', moon: '#f6f1d1', star: '#dfe6ff',
    text: '#f4f5fa', tagBg: 'rgba(8,9,14,0.72)', bubble: '#ffffff', bubbleLine: '#0d0e14', paper: '#fff8e1', flap: '#d64545', zzz: '#9fb3ff',
    clockFace: '#f2f2ec', clockRim: '#3a3f55', clockHand: '#1b1b1b', clockSec: '#d64545',
  };

  // ------------------------------------------------------------ state
  let cols = 13, rows = 9, W = 400, H = 300, ox = 0, oy = 0, scale = 1;
  let layout = null;          // furniture, spots, obstacles
  let chars = {};             // id -> character
  const flights = [];
  let frame = 0;

  // ------------------------------------------------------------ helpers
  const proj = (c, r, z = 0) => ({ x: ox + (c - r) * HW, y: oy + (c + r) * HH - z });
  const FONT = 'bold 6.5px system-ui, "Segoe UI", Roboto, sans-serif';
  const text = (str, x, y, color = PAL.text, font = FONT) => { ctx.fillStyle = color; ctx.font = font; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'; ctx.fillText(str, x, y); };
  const textWidth = (str, font = FONT) => { ctx.font = font; return ctx.measureText(str).width; };
  const ease = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
  function rr(x, y, w, h, r, fill, stroke) { ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(x, y, w, h, r); else ctx.rect(x, y, w, h); if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.6; ctx.stroke(); } }
  function circle(x, y, r, fill) { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fillStyle = fill; ctx.fill(); }
  function line(x1, y1, x2, y2, width, color) { ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
  function poly(points, fill, stroke) { ctx.beginPath(); points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.closePath(); if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.5; ctx.stroke(); } }
  function glow(x, y, r, color, alpha) { const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, color); g.addColorStop(1, 'rgba(0,0,0,0)'); ctx.save(); ctx.globalAlpha = alpha; ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2); ctx.restore(); }
  function shade(hex, f) { // f > 0 lightens, < 0 darkens
    const n = parseInt(hex.replace('#', ''), 16); if (Number.isNaN(n)) return hex;
    const ch = (v) => Math.max(0, Math.min(255, Math.round(f > 0 ? v + (255 - v) * f : v * (1 + f))));
    return `rgb(${ch(n >> 16)},${ch((n >> 8) & 255)},${ch(n & 255)})`;
  }
  /** Isometric box with footprint (c0,r0,w,h) and height z. */
  function box(c0, r0, w, h, z, color, lift = 0) {
    const t = (c, r) => proj(c, r, z + lift), b = (c, r) => proj(c, r, lift);
    poly([b(c0, r0 + h), b(c0 + w, r0 + h), t(c0 + w, r0 + h), t(c0, r0 + h)], shade(color, -0.25));   // front-left face
    poly([b(c0 + w, r0), b(c0 + w, r0 + h), t(c0 + w, r0 + h), t(c0 + w, r0)], shade(color, -0.45));   // front-right face
    poly([t(c0, r0), t(c0 + w, r0), t(c0 + w, r0 + h), t(c0, r0 + h)], shade(color, 0.12));            // top
  }
  const depthOf = (c, r) => c + r;

  // ------------------------------------------------------------ layout
  function buildLayout(agents) {
    const n = Math.max(1, agents.length);
    const perRow = 5, deskRows = Math.ceil(n / perRow);
    cols = Math.max(13, 1 + 3 * Math.min(n, perRow) + 1);
    const L = 1 + 3 * deskRows + 1;           // first lounge row
    rows = L + 4;
    const items = [], obstacles = new Set(), desks = {};
    const block = (c0, r0, w, h) => { for (let c = Math.floor(c0); c < Math.ceil(c0 + w); c++) for (let r = Math.floor(r0); r < Math.ceil(r0 + h); r++) obstacles.add(`${c},${r}`); };

    agents.forEach((a, i) => {
      const dr = 1 + 3 * Math.floor(i / perRow) + 1, dc = 1 + 3 * (i % perRow);
      desks[a.id] = { c: dc, r: dr, spot: { c: dc + 1, r: dr - 0.5 } };
      items.push({ kind: 'desk', c: dc, r: dr, w: 2, h: 1, agent: a.id });
      block(dc, dr, 2, 1);
    });
    const spots = [];
    // sofa (faces the TV on the left wall) — two seats
    items.push({ kind: 'sofa', c: 1, r: L, w: 1, h: 2 }); block(1, L, 1, 2);
    items.push({ kind: 'rug', c: 0.6, r: L - 0.3, w: 2.6, h: 2.6 });
    spots.push({ type: 'game', c: 1.55, r: L + 0.5 }, { type: 'game', c: 1.55, r: L + 1.5 });
    // beds
    [4, 6].forEach((bc) => { items.push({ kind: 'bed', c: bc, r: L, w: 1, h: 2 }); block(bc, L, 1, 2); spots.push({ type: 'sleep', c: bc + 0.5, r: L + 0.9, bed: { c: bc, r: L } }); });
    // coffee bar
    const cc = Math.min(cols - 6, 9);
    items.push({ kind: 'counter', c: cc, r: L + 2, w: 2, h: 1 }); block(cc, L + 2, 2, 1);
    spots.push({ type: 'coffee', c: cc + 0.5, r: L + 1.5 }, { type: 'coffee', c: cc + 1.5, r: L + 1.5 });
    // water cooler
    items.push({ kind: 'cooler', c: cols - 2, r: L + 2, w: 1, h: 1 }); block(cols - 2, L + 2, 1, 1);
    spots.push({ type: 'cooler', c: cols - 2.5, r: L + 2.5 }, { type: 'cooler', c: cols - 1.5, r: L + 3.5 });
    // plants
    items.push({ kind: 'plant', c: cols - 1, r: 0, w: 1, h: 1 }); block(cols - 1, 0, 1, 1);
    items.push({ kind: 'plant', c: cols - 1, r: rows - 1, w: 1, h: 1 }); block(cols - 1, rows - 1, 1, 1);
    // wander targets: free tiles in the lounge rows
    const wander = [];
    for (let r = L - 1; r < rows; r++) for (let c = 2; c < cols - 1; c++) if (!obstacles.has(`${c},${r}`)) wander.push({ type: 'wander', c: c + 0.5, r: r + 0.5 });
    spots.forEach((s) => (s.occupant = null));
    layout = { items, obstacles, desks, spots, wander, L, door: { c: 0, r: rows - 2 }, tv: { r0: L - 0.2, r1: L + 2.2 }, window: { c0: 2, c1: 5 }, clock: { c: 6.8 } };
    W = (cols + rows) * HW + PAD * 2; H = (cols + rows) * HH + WALL_H + PAD * 2 + 10;
    ox = PAD + rows * HW; oy = PAD + WALL_H;
    canvas.style.aspectRatio = `${W} / ${H}`;
    resize();
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 640;
    canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssW * dpr * H / W);
    scale = canvas.width / W;
  }
  if (window.ResizeObserver) new ResizeObserver(resize).observe(canvas); else window.addEventListener('resize', resize);

  // ------------------------------------------------------------ pathfinding (BFS on tiles)
  const key = (c, r) => `${c},${r}`;
  function findPath(from, to) {
    const start = [Math.floor(from.c), Math.floor(from.r)], goal = [Math.floor(to.c), Math.floor(to.r)];
    const free = (c, r) => c >= 0 && r >= 0 && c < cols && r < rows && !layout.obstacles.has(key(c, r));
    const prev = new Map([[key(...start), null]]);
    const queue = [start];
    while (queue.length) {
      const [c, r] = queue.shift();
      if (c === goal[0] && r === goal[1]) break;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nc = c + dc, nr = r + dr, k = key(nc, nr);
        if (prev.has(k) || (!free(nc, nr) && !(nc === goal[0] && nr === goal[1]))) continue;
        prev.set(k, [c, r]); queue.push([nc, nr]);
      }
    }
    const path = [];
    let cur = prev.has(key(...goal)) ? goal : null;
    while (cur) { path.unshift({ c: cur[0] + 0.5, r: cur[1] + 0.5 }); cur = prev.get(key(...cur)); }
    path.shift();                 // current tile
    path.push({ c: to.c, r: to.r });
    return path;
  }

  // ------------------------------------------------------------ roster & behaviour
  function setRoster(agents) {
    const old = chars; chars = {};
    buildLayout(agents);
    agents.forEach((a, i) => {
      const prev = old[a.id];
      const spot = freeSpot(['game', 'sleep', 'coffee', 'cooler']) || layout.wander[i % layout.wander.length];
      spot.occupant = a.id;
      chars[a.id] = Object.assign({ status: prev?.status || 'idle', idleSince: 0, facing: 1 }, {
        id: a.id, name: a.name, color: a.color || '#4f6df5', hair: a.hair || '#2b2b2b',
        c: spot.c, r: spot.r, spot, mode: 'lounge', dest: 'lounge', path: [], activityUntil: performance.now() + 8000 + Math.random() * 12000,
      });
    });
  }
  function freeSpot(types) {
    const free = layout.spots.filter((s) => !s.occupant && types.includes(s.type));
    if (!free.length) return null;
    return free[Math.floor(Math.random() * free.length)];
  }
  function pickActivity(c) {
    const roll = Math.random();
    let spot = null;
    if (roll < 0.55) spot = freeSpot(['game', 'sleep']);
    if (!spot && roll < 0.8) spot = freeSpot(['coffee', 'cooler']);
    if (!spot) { const w = layout.wander[Math.floor(Math.random() * layout.wander.length)]; spot = { ...w, occupant: null }; }
    return spot;
  }
  function goTo(c, spot, dest) {
    if (c.spot && c.spot.occupant === c.id) c.spot.occupant = null;
    c.spot = spot; if (spot.type !== 'wander') spot.occupant = c.id;
    c.dest = dest; c.path = findPath(c, spot); c.mode = 'walking';
  }
  function think(c, now) {
    const busy = c.status !== 'idle';
    if (busy) {
      c.idleSince = 0;
      if (c.dest !== 'desk') goTo(c, layout.desks[c.id].spot, 'desk');
    } else {
      if (!c.idleSince) c.idleSince = now;
      if (c.dest === 'desk' && now - c.idleSince > IDLE_GRACE_MS) { goTo(c, pickActivity(c), 'lounge'); c.activityUntil = now + 10000 + Math.random() * 15000; }
      else if (c.dest === 'lounge' && c.mode === 'lounge' && now > c.activityUntil) { goTo(c, pickActivity(c), 'lounge'); c.activityUntil = now + 10000 + Math.random() * 15000; }
    }
    if (c.mode === 'walking') {
      const target = c.path[0];
      if (!target) { c.mode = c.dest; return; }
      const dc = target.c - c.c, dr = target.r - c.r, dist = Math.hypot(dc, dr);
      if (dist <= SPEED) { c.c = target.c; c.r = target.r; c.path.shift(); if (!c.path.length) c.mode = c.dest; }
      else { c.c += dc / dist * SPEED; c.r += dr / dist * SPEED; c.facing = (dc - dr) < 0 ? -1 : 1; }
    }
  }

  // ------------------------------------------------------------ room
  function drawRoom() {
    ctx.fillStyle = '#14161f'; ctx.fillRect(0, 0, W, H);
    // floor
    for (let c = 0; c < cols; c++) for (let r = 0; r < rows; r++) {
      poly([proj(c, r), proj(c + 1, r), proj(c + 1, r + 1), proj(c, r + 1)], (c + r) % 2 ? PAL.floorA : PAL.floorB, PAL.floorLine);
    }
    // walls (back: r = 0 plane; left: c = 0 plane)
    const g1 = ctx.createLinearGradient(0, oy - WALL_H, 0, oy + cols * HH); g1.addColorStop(0, PAL.wallTop); g1.addColorStop(1, PAL.wallBottom);
    poly([proj(0, 0, WALL_H), proj(cols, 0, WALL_H), proj(cols, 0, 0), proj(0, 0, 0)], g1);
    const g2 = ctx.createLinearGradient(0, oy - WALL_H, 0, oy + rows * HH); g2.addColorStop(0, PAL.wallTop); g2.addColorStop(1, PAL.wallBottom);
    poly([proj(0, 0, WALL_H), proj(0, rows, WALL_H), proj(0, rows, 0), proj(0, 0, 0)], g2);
    line(proj(0, 0, WALL_H).x, proj(0, 0, WALL_H).y, proj(0, 0, 0).x, proj(0, 0, 0).y, 1, PAL.wallEdge);
    [[proj(0, 0, 0), proj(cols, 0, 0)], [proj(0, 0, 0), proj(0, rows, 0)]].forEach(([a, b]) => line(a.x, a.y, b.x, b.y, 1.2, PAL.baseboard));
    [[proj(0, 0, WALL_H), proj(cols, 0, WALL_H)], [proj(0, 0, WALL_H), proj(0, rows, WALL_H)]].forEach(([a, b]) => line(a.x, a.y, b.x, b.y, 1, PAL.wallEdge));
    // window on the back wall
    const { c0, c1 } = layout.window;
    poly([proj(c0, 0, 36), proj(c1, 0, 36), proj(c1, 0, 14), proj(c0, 0, 14)], '#3a3f55');
    ctx.save(); poly([proj(c0 + 0.15, 0, 34.5), proj(c1 - 0.15, 0, 34.5), proj(c1 - 0.15, 0, 15.5), proj(c0 + 0.15, 0, 15.5)]); ctx.clip();
    const wg = ctx.createLinearGradient(0, oy - 36, 0, oy); wg.addColorStop(0, '#0b1024'); wg.addColorStop(1, '#1a2244'); ctx.fillStyle = wg; ctx.fillRect(0, 0, W, H);
    const m = proj(c1 - 0.8, 0, 29); glow(m.x, m.y, 9, '#fff6c9', 0.35); circle(m.x, m.y, 3, PAL.moon);
    [[0.6, 31], [1.3, 24], [2.1, 30], [1.8, 19], [0.9, 21]].forEach(([dc, z]) => { const p = proj(c0 + dc, 0, z); circle(p.x, p.y, 0.5, PAL.star); });
    ctx.restore();
    const mid = (c0 + c1) / 2; line(proj(mid, 0, 36).x, proj(mid, 0, 36).y, proj(mid, 0, 14).x, proj(mid, 0, 14).y, 1, '#3a3f55');
    line(proj(c0, 0, 25).x, proj(c0, 0, 25).y, proj(c1, 0, 25).x, proj(c1, 0, 25).y, 1, '#3a3f55');
    // door on the left wall
    const d = layout.door;
    poly([proj(0, d.r - 0.1, 34), proj(0, d.r + 1.3, 34), proj(0, d.r + 1.3, 0), proj(0, d.r - 0.1, 0)], '#3a3f55');
    poly([proj(0, d.r, 32), proj(0, d.r + 1.2, 32), proj(0, d.r + 1.2, 0), proj(0, d.r, 0)], '#4a3524');
    const knob = proj(0, d.r + 1.0, 15); circle(knob.x, knob.y, 1, PAL.lamp);
    // TV on the left wall
    const tv = layout.tv, gaming = Object.values(chars).some((c) => c.mode === 'lounge' && c.spot?.type === 'game');
    poly([proj(0, tv.r0, 30), proj(0, tv.r1, 30), proj(0, tv.r1, 14), proj(0, tv.r0, 14)], PAL.tv);
    if (gaming) {
      ctx.save(); poly([proj(0, tv.r0 + 0.1, 29), proj(0, tv.r1 - 0.1, 29), proj(0, tv.r1 - 0.1, 15), proj(0, tv.r0 + 0.1, 15)]); ctx.clip();
      ctx.fillStyle = '#1b2f58'; ctx.fillRect(0, 0, W, H);
      for (let i = 0; i < 7; i++) { const seed = ((frame >> 3) * 7 + i * 13) % 17; const p = proj(0, tv.r0 + 0.3 + (seed % 5) * 0.4, 18 + (i % 3) * 3.5); circle(p.x, p.y, 1.3, ['#ff6b6b', '#ffd93d', '#6bffb8', '#6bb8ff'][(seed + i) % 4]); }
      ctx.restore();
      const g = proj(0.8, (tv.r0 + tv.r1) / 2, 10); glow(g.x, g.y, 30, '#9fd0ff', 0.18);
    } else poly([proj(0, tv.r0 + 0.1, 29), proj(0, tv.r1 - 0.1, 29), proj(0, tv.r1 - 0.1, 15), proj(0, tv.r0 + 0.1, 15)], '#05060a');
    // sign + clock on the back wall
    const sc = cols - 4.6; poly([proj(sc, 0, 41), proj(sc + 3.8, 0, 41), proj(sc + 3.8, 0, 29), proj(sc, 0, 29)], '#3a2a1a');
    const st = proj(sc + 1.9, 0, 33); rr(st.x - 23, st.y - 6.5, 46, 9, 2, '#5a4030'); text('AGENTORIUM', st.x, st.y, '#f0d9a0', 'bold 5.5px system-ui, sans-serif');
    drawClock(proj(layout.clock.c, 0, 37), 6.5);
    // rug
    layout.items.filter((i) => i.kind === 'rug').forEach((i) => poly([proj(i.c, i.r), proj(i.c + i.w, i.r), proj(i.c + i.w, i.r + i.h), proj(i.c, i.r + i.h)], PAL.rug, PAL.rugEdge));
    // light pools under the desk lamps
    Object.values(layout.desks).forEach((d) => { const p = proj(d.c + 1, d.r + 0.2); glow(p.x, p.y, 34, '#ffe9a8', 0.10); });
  }

  function drawClock(p, r) {
    const now = new Date();
    circle(p.x, p.y, r + 1.4, PAL.clockRim); circle(p.x, p.y, r, PAL.clockFace);
    for (let i = 0; i < 12; i++) { const a = i * Math.PI / 6; line(p.x + Math.sin(a) * (r - 1), p.y - Math.cos(a) * (r - 1), p.x + Math.sin(a) * (r - 2), p.y - Math.cos(a) * (r - 2), 0.5, PAL.clockHand); }
    const sec = now.getSeconds() + now.getMilliseconds() / 1000, min = now.getMinutes() + sec / 60, hr = (now.getHours() % 12) + min / 60;
    const hand = (angle, len, width, color) => line(p.x, p.y, p.x + Math.sin(angle) * len, p.y - Math.cos(angle) * len, width, color);
    hand(hr * Math.PI / 6, r * 0.5, 1.3, PAL.clockHand); hand(min * Math.PI / 30, r * 0.75, 0.9, PAL.clockHand); hand(sec * Math.PI / 30, r * 0.85, 0.4, PAL.clockSec);
    circle(p.x, p.y, 0.8, PAL.clockSec);
  }

  // ------------------------------------------------------------ furniture
  function drawItem(i) {
    if (i.kind === 'desk') {
      const busy = chars[i.agent] && chars[i.agent].status !== 'idle' && chars[i.agent].mode === 'desk';
      box(i.c, i.r, 2, 1, 12, PAL.desk);
      box(i.c + 0.2, i.r + 0.45, 0.7, 0.35, 1.2, PAL.key, 12);                                   // keyboard
      box(i.c + 1.15, i.r + 0.15, 0.75, 0.12, 12, PAL.monitor, 12);                                // monitor
      const s0 = proj(i.c + 1.2, i.r + 0.15, 23), s1 = proj(i.c + 1.85, i.r + 0.15, 23), s2 = proj(i.c + 1.85, i.r + 0.15, 14), s3 = proj(i.c + 1.2, i.r + 0.15, 14);
      // the screen faces the agent (away from the viewer); show its glow and a sliver of colour
      poly([s0, s1, s2, s3], busy ? '#1f6b4e' : '#20253d');
      if (busy) { const g = proj(i.c + 1.5, i.r - 0.3, 14); glow(g.x, g.y, 20, PAL.screenOn, 0.25); }
      const lamp = proj(i.c + 1, i.r + 0.5, 60); line(lamp.x, lamp.y - 60, lamp.x, lamp.y, 0.7, '#6b6f80');
      ctx.beginPath(); ctx.moveTo(lamp.x - 4, lamp.y); ctx.lineTo(lamp.x + 4, lamp.y); ctx.lineTo(lamp.x + 7, lamp.y + 5); ctx.lineTo(lamp.x - 7, lamp.y + 5); ctx.closePath(); ctx.fillStyle = PAL.lamp; ctx.fill();
      glow(lamp.x, lamp.y + 5, 12, '#ffe9a8', 0.4);
    } else if (i.kind === 'sofa') {
      box(i.c, i.r, 1, 2, 8, PAL.sofa);                       // seat
      box(i.c + 0.7, i.r, 0.3, 2, 16, shade(PAL.sofa, -0.1));  // backrest (away from TV)
      box(i.c, i.r - 0.25, 1, 0.25, 12, shade(PAL.sofa, -0.1)); box(i.c, i.r + 2, 1, 0.25, 12, shade(PAL.sofa, -0.1)); // armrests
    } else if (i.kind === 'bed') {
      box(i.c, i.r, 1, 2, 7, PAL.bedFrame); box(i.c + 0.05, i.r + 0.05, 0.9, 1.9, 4, PAL.mattress, 7);
      box(i.c + 0.15, i.r + 0.1, 0.7, 0.45, 2.5, PAL.pillow, 11);
      const sleeper = Object.values(chars).find((c) => c.mode === 'lounge' && c.spot?.bed && c.spot.bed.c === i.c);
      if (sleeper) {
        const breathe = Math.sin(frame * 0.08) * 0.6;
        box(i.c + 0.08, i.r + 0.75, 0.84, 1.2, 5 + breathe, PAL.blanket, 11);
        const hp = proj(i.c + 0.5, i.r + 0.5, 16); drawHead(hp.x, hp.y, sleeper, 0, true);
        const z = (frame % 90) / 90; ctx.save(); ctx.globalAlpha = 1 - z; text('z', hp.x + 6 + z * 4, hp.y - 8 - z * 10, PAL.zzz); ctx.restore();
        tag(sleeper.name, hp.x, hp.y - 10);
      }
    } else if (i.kind === 'counter') {
      box(i.c, i.r, 2, 1, 13, PAL.counter);
      box(i.c + 1.2, i.r + 0.2, 0.6, 0.6, 9, PAL.machine, 13); const led = proj(i.c + 1.8, i.r + 0.5, 19); circle(led.x, led.y, 0.7, '#ff5a5a');
      box(i.c + 0.25, i.r + 0.3, 0.25, 0.25, 2.5, '#f4f6fb', 13); box(i.c + 0.6, i.r + 0.3, 0.25, 0.25, 2.5, '#f4f6fb', 13); // cups
    } else if (i.kind === 'cooler') {
      box(i.c + 0.25, i.r + 0.25, 0.5, 0.5, 16, PAL.cooler); box(i.c + 0.3, i.r + 0.3, 0.4, 0.4, 8, PAL.coolerWater, 16);
    } else if (i.kind === 'plant') {
      box(i.c + 0.3, i.r + 0.3, 0.4, 0.4, 6, PAL.plantPot);
      const p = proj(i.c + 0.5, i.r + 0.5, 6);
      [[0, -8, 5], [-4, -4, 4], [4, -5, 4], [-2, -11, 3.5], [3, -10, 3.5]].forEach(([dx, dy, r]) => circle(p.x + dx, p.y + dy, r, shade(PAL.plant, (dy + 8) / 40)));
    }
  }

  // ------------------------------------------------------------ characters
  function drawHead(cx, cy, c, look = 0, asleep = false) {
    circle(cx, cy, 5, PAL.skin);
    ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, 5, 0, Math.PI * 2); ctx.clip();
    circle(cx, cy - 2.5, 5, c.hair); ctx.fillStyle = c.hair; ctx.fillRect(cx - 5.5, cy - 8, 11, 6); ctx.restore();
    const blink = asleep || (frame % 160) < 6;
    if (blink) { line(cx - 2.5 + look, cy + 0.6, cx - 1.2 + look, cy + 0.6, 0.7, PAL.eye); line(cx + 1.2 + look, cy + 0.6, cx + 2.5 + look, cy + 0.6, 0.7, PAL.eye); }
    else { circle(cx - 1.8 + look, cy + 0.4, 0.8, PAL.eye); circle(cx + 1.8 + look, cy + 0.4, 0.8, PAL.eye); }
    ctx.strokeStyle = PAL.mouth; ctx.lineWidth = 0.6; ctx.beginPath(); ctx.arc(cx + look * 0.5, cy + 2.1, 1.3, 0.15 * Math.PI, 0.85 * Math.PI); ctx.stroke();
  }
  /** Standing / walking / typing figure with feet at screen point (fx, fy). Returns head top y. */
  function drawFigure(c, fx, fy, pose) {
    const t = frame * 0.28, walking = pose === 'walk';
    const swing = walking ? Math.sin(t) * 2.2 : 0, bob = walking ? Math.abs(Math.sin(t)) * 0.8 : Math.sin(frame * 0.05) * 0.3;
    const hipY = fy - 7 - bob, look = c.facing < 0 ? -1 : 1;
    line(fx - 1.6, hipY, fx - 1.6 - swing, fy - 0.5, 2.2, PAL.pants); line(fx + 1.6, hipY, fx + 1.6 + swing, fy - 0.5, 2.2, PAL.pants);
    circle(fx - 1.6 - swing, fy - 0.5, 1.2, PAL.shoe); circle(fx + 1.6 + swing, fy - 0.5, 1.2, PAL.shoe);
    rr(fx - 4, hipY - 8, 8, 9, 2.5, c.color);
    let armL, armR;
    if (pose === 'type') { const k = Math.sin(frame * 0.5); armL = { x: fx - 3 + look * 2, y: hipY - 4 + k * 0.5 }; armR = { x: fx + 3 + look * 2, y: hipY - 4 - k * 0.5 }; }
    else if (pose === 'coffee') { armL = { x: fx - 4, y: hipY - 1 }; armR = { x: fx + 3.5, y: hipY - 6 }; }
    else { armL = { x: fx - 4 - swing * 0.6, y: hipY - 1 }; armR = { x: fx + 4 + swing * 0.6, y: hipY - 1 }; }
    line(fx - 3.5, hipY - 6.5, armL.x, armL.y, 2, c.color); circle(armL.x, armL.y, 1.1, PAL.skin);
    line(fx + 3.5, hipY - 6.5, armR.x, armR.y, 2, c.color); circle(armR.x, armR.y, 1.1, PAL.skin);
    if (pose === 'coffee') { rr(armR.x - 1.5, armR.y - 3.5, 3.5, 3, 0.8, '#f4f6fb'); const s = (frame % 60) / 60; ctx.save(); ctx.globalAlpha = 1 - s; circle(armR.x + 0.5, armR.y - 5 - s * 5, 0.8, '#ccc'); ctx.restore(); }
    drawHead(fx, hipY - 13, c, look * 0.7);
    return hipY - 18.5;
  }
  function drawSitting(c, fx, fy) {
    const lean = Math.sin(frame * 0.12) * 0.4, seatY = fy - 5;
    line(fx - 1.5, seatY, fx + 4, seatY + 3, 2.2, PAL.pants); line(fx + 1.5, seatY, fx + 6, seatY + 3.5, 2.2, PAL.pants);
    circle(fx + 4.5, seatY + 3.5, 1.2, PAL.shoe); circle(fx + 6.5, seatY + 4, 1.2, PAL.shoe);
    rr(fx - 4, seatY - 9 - lean, 8, 9.5, 2.5, c.color);
    const w = Math.sin(frame * 0.4) * 0.5;
    line(fx - 3, seatY - 7 - lean, fx + 2.5, seatY - 3.5 + w, 2, c.color); line(fx + 3, seatY - 7 - lean, fx + 5.5, seatY - 3.5 + w, 2, c.color);
    rr(fx + 1.5, seatY - 5 + w, 6, 3, 1.2, '#8a8f9f'); circle(fx + 3, seatY - 3.5 + w, 0.5, '#ff6b6b'); circle(fx + 6, seatY - 3.5 + w, 0.5, '#6bb8ff');
    drawHead(fx, seatY - 14.5 - lean, c, 1);
    return seatY - 20 - lean;
  }
  function drawChar(c) {
    if (c.mode === 'lounge' && c.spot?.type === 'sleep') return;   // drawn with the bed
    const f = proj(c.c, c.r);
    const busy = c.status !== 'idle';
    let top;
    if (c.mode === 'lounge' && c.spot?.type === 'game') top = drawSitting(c, f.x, f.y);
    else if (c.mode === 'lounge' && c.spot?.type === 'coffee') top = drawFigure(c, f.x, f.y, 'coffee');
    else if (c.mode === 'desk') top = drawFigure(c, f.x, f.y, busy ? 'type' : 'stand');
    else top = drawFigure(c, f.x, f.y, c.mode === 'walking' ? 'walk' : 'stand');
    overlays.push(() => {
      tag(c.name, f.x, top - 3);
      if (busy) drawBubble(f.x, top - 13, c.status, true);
      else if (c.mode === 'lounge' && c.spot?.type === 'cooler' && ((frame >> 6) % 3) === 0) drawBubble(f.x, top - 13, '…', false);
    });
  }

  function tag(str, cx, baseline) {
    const w = textWidth(str) + 6, x = Math.min(Math.max(1, cx - w / 2), W - w - 1);
    rr(x, baseline - 7, w, 9.5, 3, PAL.tagBg); text(str, x + w / 2, baseline);
  }
  function drawBubble(cx, bottom, label, animatedDots) {
    const str = label + (animatedDots ? '.'.repeat(1 + ((frame >> 4) % 3)) : '');
    const w = Math.max(16, textWidth(str) + 7), x = Math.min(Math.max(2, cx - w / 2), W - w - 2), y = bottom - 11.5;
    rr(x, y, w, 9.5, 3.5, PAL.bubble, PAL.bubbleLine);
    ctx.beginPath(); ctx.moveTo(cx - 2, y + 9.3); ctx.lineTo(cx, y + 12); ctx.lineTo(cx + 2, y + 9.3); ctx.closePath(); ctx.fillStyle = PAL.bubble; ctx.fill();
    text(str, x + w / 2, y + 7, '#1b1b1b');
  }
  function drawEnvelope(x, y) {
    rr(x - 4.5, y - 3, 9, 6, 1, PAL.paper, PAL.bubbleLine);
    ctx.strokeStyle = PAL.flap; ctx.lineWidth = 0.7; ctx.beginPath(); ctx.moveTo(x - 4.5, y - 3); ctx.lineTo(x, y + 0.5); ctx.lineTo(x + 4.5, y - 3); ctx.stroke();
  }
  const anchorPos = (id) => { if (id === 'user') { const d = layout.door; return proj(0.3, d.r + 0.6, 12); } const c = chars[id]; return c ? proj(c.c, c.r, 18) : null; };
  function drawFlights() {
    for (let i = flights.length - 1; i >= 0; i--) {
      const f = flights[i], raw = Math.min(1, (performance.now() - f.start) / 1200), p = ease(raw);
      const a = anchorPos(f.from), b = anchorPos(f.to);
      if (!a || !b) { flights.splice(i, 1); continue; }
      const x = a.x + (b.x - a.x) * p, y = a.y + (b.y - a.y) * p - Math.sin(p * Math.PI) * 30;
      glow(x, y, 6, '#fff3b0', 0.5); drawEnvelope(x, y);
      if (raw < 0.45 && f.label) drawBubble(a.x, a.y - 6, f.label, false);
      if (raw >= 1) flights.splice(i, 1);
    }
  }

  // ------------------------------------------------------------ main loop
  let overlays = [];
  function loop() {
    frame++;
    if (layout) {
      const now = performance.now();
      Object.values(chars).forEach((c) => think(c, now));
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      overlays = [];
      drawRoom();
      const drawables = [
        ...layout.items.filter((i) => i.kind !== 'rug').map((i) => ({ depth: depthOf(i.c + i.w, i.r + i.h), draw: () => drawItem(i) })),
        ...Object.values(chars).map((c) => ({ depth: depthOf(c.c, c.r) + 1 + (c.mode === 'lounge' && c.spot?.type === 'game' ? 0.6 : 0), draw: () => drawChar(c) })),
      ];
      drawables.sort((a, b) => a.depth - b.depth).forEach((d) => d.draw());
      overlays.forEach((fn) => fn());
      drawFlights();
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  return {
    setRoster,
    setStatus(agentId, status) { if (chars[agentId]) chars[agentId].status = status || 'idle'; },
    send(from, to, label) { if (layout) flights.push({ from, to, label: label || '', start: performance.now() }); },
  };
})();
