"""Lightweight realtime labeling page + endpoints (no Next.js, no build step).

Serves one self-contained HTML page at ``GET /label`` where the operator draws
region boxes on a fresh device frame and either:

* **Send hint** — pushes the bbox(es) to Redis (``wos:label:hints``) so the agent
  can pick them up headlessly via ``botctl label-hints`` and act; or
* **Commit** — writes the region(s) straight into the module ``area.yaml`` + crop
  via :func:`api.services.labeling.commit_region_from_frame`.

The frame is captured with ``adb_screencap_png`` (no scrcpy → safe alongside a
running worker) and pinned so the commit crops exactly what was drawn on.
"""
from __future__ import annotations

import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from api.deps import get_redis
from api.services import labeling as labeling_svc

router = APIRouter(tags=["label-live"])

LABEL_HINTS_KEY = "wos:label:hints"


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class RegionInput(BaseModel):
    name: str
    action: str = "exist"
    type: str | None = None
    threshold: float = 0.9
    has_red_dot: bool = False
    bbox: dict[str, Any] = Field(default_factory=dict)


class HintBody(BaseModel):
    instance_id: str = ""
    screen_id: str = ""
    ref: str | None = None
    scope: str = "core"
    game: str | None = None
    regions: list[RegionInput] = Field(default_factory=list)
    note: str = ""


class CommitBody(HintBody):
    mode: str = "surgical"
    version: str | None = None


def _pin_game_from(game: str | None, instance_id: str | None) -> str:
    """Resolve the active game (explicit or via instance) and pin the contextvar."""
    from api.services.game_resolver import resolve_game, set_current_request_game
    from config.games import default_game

    try:
        resolved = resolve_game(game=game, instance_id=instance_id) or default_game()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_current_request_game(resolved)
    return resolved


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/label")
def label_page() -> HTMLResponse:
    return HTMLResponse(_PAGE_HTML)


@router.get("/api/label/init")
def label_init(
    inst: str = Query(default=""),
    scope: str = Query(default="core"),
    game: str | None = Query(default=None),
) -> dict[str, Any]:
    g = _pin_game_from(game, inst or None)
    from api.services.instances import list_instance_ids

    try:
        instances = list_instance_ids()
    except Exception:
        instances = []
    refs = labeling_svc.list_reference_paths(scope=scope, limit=500)
    screens = [
        {
            "screen_id": r.get("screen_id") or "",
            "ref": r.get("rel") or "",
            "title": r.get("title") or r.get("name") or "",
            "region_count": r.get("region_count") or 0,
        }
        for r in refs
        if r.get("screen_id")
    ]
    screens.sort(key=lambda s: (s["screen_id"], s["ref"]))
    return {
        "instances": instances,
        "default_instance": inst or (instances[0] if instances else ""),
        "scope": scope,
        "game": g,
        "scopes": labeling_svc.list_labeling_scopes(),
        "screens": screens,
    }


@router.get("/api/label/frame")
def label_frame(
    inst: str = Query(...),
    fresh: bool = Query(default=True),
    game: str | None = Query(default=None),
) -> Response:
    _pin_game_from(game, inst)
    try:
        if fresh:
            png = labeling_svc.capture_and_pin_frame(inst)
        else:
            png = labeling_svc.read_pinned_frame(inst) or labeling_svc.capture_and_pin_frame(inst)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/label/regions")
def label_regions(
    ref: str = Query(...),
    scope: str = Query(default="core"),
    game: str | None = Query(default=None),
) -> dict[str, Any]:
    _pin_game_from(game, None)
    try:
        return labeling_svc.get_labeling_document(ref, scope=scope)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/label/hint")
def label_hint(body: HintBody) -> dict[str, Any]:
    _pin_game_from(body.game, body.instance_id or None)
    payload = {
        "ts": time.time(),
        "instance_id": body.instance_id,
        "screen_id": body.screen_id,
        "ref": body.ref,
        "scope": body.scope,
        "game": body.game,
        "note": body.note,
        "regions": [r.model_dump() for r in body.regions],
        "committed": False,
    }
    try:
        get_redis().lpush(LABEL_HINTS_KEY, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unreachable: {exc}") from exc
    return {"ok": True, "queued": len(body.regions)}


@router.post("/api/label/commit")
def label_commit(body: CommitBody) -> dict[str, Any]:
    _pin_game_from(body.game, body.instance_id or None)
    try:
        result = labeling_svc.commit_region_from_frame(
            instance_id=body.instance_id,
            regions=[r.model_dump() for r in body.regions],
            ref=body.ref,
            screen_id=body.screen_id,
            scope=body.scope,
            mode=body.mode,
            version=body.version,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Log a committed hint so the agent can see what the operator just did.
    with contextlib.suppress(Exception):
        get_redis().lpush(
            LABEL_HINTS_KEY,
            json.dumps(
                {
                    "ts": time.time(),
                    "instance_id": body.instance_id,
                    "screen_id": result.get("screen_id", body.screen_id),
                    "ref": result.get("ref"),
                    "scope": body.scope,
                    "game": body.game,
                    "note": body.note,
                    "regions": [r.model_dump() for r in body.regions],
                    "committed": True,
                },
                ensure_ascii=False,
            ),
        )
    return result


# --------------------------------------------------------------------------- #
# The page — vanilla HTML/CSS/JS, one constant, no framework.
# --------------------------------------------------------------------------- #
_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live Label</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
         background:#0d1117; color:#c9d1d9; }
  header { padding:10px 14px; border-bottom:1px solid #21262d; display:flex; gap:14px;
           align-items:center; flex-wrap:wrap; position:sticky; top:0; background:#0d1117; z-index:5; }
  h1 { font-size:14px; margin:0; color:#58a6ff; font-weight:600; }
  main { display:flex; gap:16px; padding:14px; align-items:flex-start; flex-wrap:wrap; }
  label { color:#8b949e; }
  select, input[type=text], input[type=number] {
    background:#161b22; color:#c9d1d9; border:1px solid #30363d; border-radius:6px;
    padding:4px 7px; font:inherit; }
  input[type=text]:focus, select:focus, input[type=number]:focus { outline:1px solid #1f6feb; }
  button { background:#21262d; color:#c9d1d9; border:1px solid #30363d; border-radius:6px;
           padding:5px 11px; font:inherit; cursor:pointer; }
  button:hover { background:#30363d; }
  button.primary { background:#1f6feb; border-color:#1f6feb; color:#fff; }
  button.primary:hover { background:#388bfd; }
  button.warn { background:#9e6a03; border-color:#9e6a03; color:#fff; }
  .frameWrap { position:relative; width:360px; flex:0 0 auto; user-select:none;
               border:1px solid #30363d; border-radius:8px; overflow:hidden; }
  #frame { display:block; width:360px; height:640px; background:#010409; }
  #overlay { position:absolute; inset:0; width:360px; height:640px; cursor:crosshair; }
  .side { flex:1 1 300px; min-width:280px; display:flex; flex-direction:column; gap:12px; }
  .card { border:1px solid #21262d; border-radius:8px; padding:12px; background:#0f141a; }
  .card h2 { font-size:12px; margin:0 0 8px; color:#8b949e; text-transform:uppercase; letter-spacing:.04em; }
  .row { display:flex; gap:8px; align-items:center; margin-bottom:7px; flex-wrap:wrap; }
  .row > label { width:62px; flex:0 0 auto; }
  .grow { flex:1 1 auto; }
  .readout { font-size:12px; color:#7d8590; }
  .pill { padding:1px 6px; border-radius:10px; background:#161b22; border:1px solid #30363d; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { text-align:left; padding:3px 5px; border-bottom:1px solid #161b22; }
  th { color:#6e7681; font-weight:500; }
  td.x { color:#f85149; cursor:pointer; width:20px; text-align:center; }
  .muted { color:#6e7681; }
  #toast { position:fixed; right:14px; bottom:14px; max-width:380px; padding:10px 12px;
           border-radius:8px; border:1px solid #30363d; background:#161b22; display:none;
           white-space:pre-wrap; font-size:12px; }
  #mag { width:150px; height:150px; border:1px solid #30363d; border-radius:6px; background:#010409; }
  .magWrap { display:flex; gap:10px; align-items:flex-start; }
  .chk { display:flex; gap:6px; align-items:center; }
</style>
</head>
<body>
<header>
  <h1>● live label</h1>
  <div class="row" style="margin:0">
    <label>inst</label><select id="inst"></select>
    <label>scope</label><select id="scope"></select>
    <label>screen</label><select id="screen" class="grow"></select>
    <button id="refresh">↻ frame</button>
    <span id="frameAge" class="readout"></span>
  </div>
</header>
<main>
  <div class="frameWrap">
    <img id="frame" alt="device frame">
    <canvas id="overlay" width="720" height="1280"></canvas>
  </div>

  <div class="side">
    <div class="card">
      <h2>cursor / draw</h2>
      <div class="magWrap">
        <canvas id="mag" width="150" height="150"></canvas>
        <div>
          <div class="readout">px <span id="rdPx" class="pill">—</span></div>
          <div class="readout" style="margin-top:5px">% <span id="rdPct" class="pill">—</span></div>
          <div class="readout" style="margin-top:5px">box <span id="rdBox" class="pill">—</span></div>
          <div class="chk" style="margin-top:9px"><input type="checkbox" id="grid" checked><label for="grid">10% grid</label></div>
          <div class="muted" style="margin-top:6px">drag on the frame to draw a box</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>region defaults</h2>
      <div class="row"><label>name</label><input type="text" id="rName" class="grow" placeholder="e.g. mail.claim"></div>
      <div class="row">
        <label>action</label>
        <select id="rAction">
          <option>exist</option><option>text</option><option>click</option><option>color_check</option>
        </select>
        <label>type</label>
        <input type="text" id="rType" placeholder="string / integer / time / red" class="grow">
      </div>
      <div class="row">
        <label>thresh</label><input type="number" id="rThresh" value="0.9" step="0.01" min="0" max="1" style="width:80px">
        <span class="chk"><input type="checkbox" id="rRed"><label for="rRed">red dot</label></span>
      </div>
    </div>

    <div class="card">
      <h2>batch <span class="muted" id="batchN"></span></h2>
      <table id="batch"><thead><tr><th>name</th><th>act</th><th>bbox %</th><th></th></tr></thead><tbody></tbody></table>
      <div class="muted" id="batchEmpty">— draw a box to add —</div>
    </div>

    <div class="card">
      <div class="row">
        <button id="sendHint" class="primary">Send hint → Claude</button>
        <button id="commit">Commit</button>
        <select id="mode"><option value="surgical">surgical</option><option value="recapture_reference">recapture ref</option></select>
        <button id="clearBatch">clear</button>
      </div>
      <div class="muted">Hint → Redis (botctl label-hints). Commit → area.yaml + crop.</div>
    </div>
  </div>
</main>
<div id="toast"></div>

<script>
const FW = 720, FH = 1280;
const $ = (id) => document.getElementById(id);
const img = $("frame"), cv = $("overlay"), ctx = cv.getContext("2d");
const mag = $("mag"), mctx = mag.getContext("2d");
let SCREENS = [], boxes = [], existing = [], drag = null, hover = -1;

function toast(msg, ok=true) {
  const t = $("toast"); t.textContent = msg; t.style.display = "block";
  t.style.borderColor = ok ? "#238636" : "#f85149";
  clearTimeout(toast._t); toast._t = setTimeout(() => t.style.display = "none", 6000);
}
function curRef() {
  const o = $("screen").selectedOptions[0];
  return o ? o.dataset.ref || "" : "";
}
function curScreenId() {
  const o = $("screen").selectedOptions[0];
  return o ? o.dataset.sid || "" : "";
}

async function init() {
  const r = await fetch("/api/label/init?scope=core");
  const d = await r.json();
  $("inst").innerHTML = d.instances.map(i => `<option>${i}</option>`).join("");
  if (d.default_instance) $("inst").value = d.default_instance;
  $("scope").innerHTML = (d.scopes || []).map(s => `<option value="${s.key}">${s.key}</option>`).join("");
  $("scope").value = d.scope || "core";
  loadScreens(d.screens);
  loadFrame(true);
}
function loadScreens(screens) {
  SCREENS = screens || [];
  $("screen").innerHTML = SCREENS.map((s, i) =>
    `<option value="${i}" data-ref="${s.ref}" data-sid="${s.screen_id}">${s.screen_id} · ${s.region_count}r</option>`
  ).join("");
  onScreenChange();
}
async function reloadScreensForScope() {
  const r = await fetch(`/api/label/init?scope=${encodeURIComponent($("scope").value)}&inst=${encodeURIComponent($("inst").value)}`);
  const d = await r.json();
  loadScreens(d.screens);
}
async function onScreenChange() {
  existing = [];
  redraw();
  const ref = curRef();
  if (!ref) return;
  try {
    const r = await fetch(`/api/label/regions?ref=${encodeURIComponent(ref)}&scope=${encodeURIComponent($("scope").value)}`);
    if (r.ok) { const d = await r.json(); existing = (d.regions || []).filter(x => x.bbox); redraw(); }
  } catch (e) { /* ignore */ }
}

function loadFrame(fresh) {
  const inst = $("inst").value;
  img.onload = () => { $("frameAge").textContent = fresh ? "fresh" : "pinned"; redraw(); };
  img.onerror = () => toast("frame capture failed (is the device online?)", false);
  img.src = `/api/label/frame?inst=${encodeURIComponent(inst)}&fresh=${fresh ? 1 : 0}&t=${Date.now()}`;
}

// ---- coordinate mapping (CSS-scale-proof) ----
function evtToFrame(e) {
  const r = cv.getBoundingClientRect();
  let fx = (e.clientX - r.left) / r.width * FW;
  let fy = (e.clientY - r.top) / r.height * FH;
  fx = Math.max(0, Math.min(FW, fx));
  fy = Math.max(0, Math.min(FH, fy));
  return { fx, fy };
}
function rectToBboxPct(a, b) {
  const x = Math.min(a.fx, b.fx), y = Math.min(a.fy, b.fy);
  const w = Math.abs(a.fx - b.fx), h = Math.abs(a.fy - b.fy);
  return { x: x / FW * 100, y: y / FH * 100, width: w / FW * 100, height: h / FH * 100,
           rotation: 0, original_width: FW, original_height: FH };
}
function bboxPx(b) {
  return [b.x / 100 * FW, b.y / 100 * FH, b.width / 100 * FW, b.height / 100 * FH];
}

function redraw() {
  ctx.clearRect(0, 0, FW, FH);
  if ($("grid").checked) {
    ctx.strokeStyle = "rgba(88,166,255,.16)"; ctx.lineWidth = 1;
    for (let i = 1; i < 10; i++) {
      ctx.beginPath(); ctx.moveTo(i / 10 * FW, 0); ctx.lineTo(i / 10 * FW, FH); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, i / 10 * FH); ctx.lineTo(FW, i / 10 * FH); ctx.stroke();
    }
  }
  existing.forEach(r => { const [x, y, w, h] = bboxPx(r.bbox);
    ctx.strokeStyle = "rgba(125,133,144,.5)"; ctx.lineWidth = 2; ctx.strokeRect(x, y, w, h); });
  boxes.forEach((b, i) => { const [x, y, w, h] = bboxPx(b.bbox);
    ctx.strokeStyle = i === hover ? "#f0883e" : "#3fb950"; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = ctx.strokeStyle; ctx.font = "20px monospace";
    ctx.fillText(b.name || "?", x + 3, y > 22 ? y - 6 : y + 22); });
  if (drag) { const bb = rectToBboxPct(drag.a, drag.b); const [x, y, w, h] = bboxPx(bb);
    ctx.strokeStyle = "#58a6ff"; ctx.setLineDash([6, 4]); ctx.lineWidth = 2; ctx.strokeRect(x, y, w, h); ctx.setLineDash([]); }
}

// ---- magnifier + readout ----
function updateMag(fx, fy) {
  const Z = 6, span = mag.width / Z;
  mctx.imageSmoothingEnabled = false;
  mctx.clearRect(0, 0, mag.width, mag.height);
  if (img.naturalWidth) {
    const sx = Math.max(0, Math.min(img.naturalWidth - span, fx / FW * img.naturalWidth - span / 2));
    const sy = Math.max(0, Math.min(img.naturalHeight - span, fy / FH * img.naturalHeight - span / 2));
    mctx.drawImage(img, sx, sy, span, span, 0, 0, mag.width, mag.height);
  }
  mctx.strokeStyle = "rgba(248,81,73,.9)"; mctx.lineWidth = 1;
  mctx.beginPath(); mctx.moveTo(mag.width/2, 0); mctx.lineTo(mag.width/2, mag.height); mctx.stroke();
  mctx.beginPath(); mctx.moveTo(0, mag.height/2); mctx.lineTo(mag.width, mag.height/2); mctx.stroke();
  $("rdPx").textContent = `${fx.toFixed(0)}, ${fy.toFixed(0)}`;
  $("rdPct").textContent = `${(fx/FW*100).toFixed(1)}, ${(fy/FH*100).toFixed(1)}`;
}

cv.addEventListener("pointerdown", (e) => { cv.setPointerCapture(e.pointerId);
  const p = evtToFrame(e); drag = { a: p, b: p }; });
cv.addEventListener("pointermove", (e) => { const p = evtToFrame(e); updateMag(p.fx, p.fy);
  if (drag) { drag.b = p; const bb = rectToBboxPct(drag.a, drag.b);
    $("rdBox").textContent = `${bb.width.toFixed(1)}×${bb.height.toFixed(1)}`; redraw(); } });
cv.addEventListener("pointerup", (e) => {
  if (!drag) return; const bb = rectToBboxPct(drag.a, drag.b); drag = null;
  if (bb.width < 0.4 || bb.height < 0.4) { redraw(); return; }
  const name = ($("rName").value || "").trim();
  boxes.push({ name, action: $("rAction").value, type: ($("rType").value || "").trim() || null,
    threshold: parseFloat($("rThresh").value) || 0.9, has_red_dot: $("rRed").checked, bbox: bb });
  renderBatch(); redraw();
});

function renderBatch() {
  const tb = $("batch").querySelector("tbody");
  tb.innerHTML = boxes.map((b, i) =>
    `<tr data-i="${i}"><td>${b.name || '<span class="muted">(unnamed)</span>'}</td><td>${b.action}</td>` +
    `<td class="muted">${b.bbox.x.toFixed(1)},${b.bbox.y.toFixed(1)} ${b.bbox.width.toFixed(1)}×${b.bbox.height.toFixed(1)}</td>` +
    `<td class="x" data-del="${i}">✕</td></tr>`).join("");
  $("batchEmpty").style.display = boxes.length ? "none" : "block";
  $("batchN").textContent = boxes.length ? `(${boxes.length})` : "";
  tb.querySelectorAll("tr").forEach(tr => {
    tr.onmouseenter = () => { hover = +tr.dataset.i; redraw(); };
    tr.onmouseleave = () => { hover = -1; redraw(); };
  });
  tb.querySelectorAll("[data-del]").forEach(td => td.onclick = (e) => {
    e.stopPropagation(); boxes.splice(+td.dataset.del, 1); renderBatch(); redraw(); });
}

function payload() {
  return { instance_id: $("inst").value, screen_id: curScreenId(), ref: curRef() || null,
    scope: $("scope").value, regions: boxes.map(b => ({ name: b.name, action: b.action, type: b.type,
      threshold: b.threshold, has_red_dot: b.has_red_dot, bbox: b.bbox })) };
}
function validate() {
  if (!boxes.length) { toast("draw at least one box first", false); return false; }
  const bad = boxes.find(b => !b.name); if (bad) { toast("every box needs a name", false); return false; }
  if (!curRef() && !curScreenId()) { toast("pick a screen", false); return false; }
  return true;
}

$("sendHint").onclick = async () => {
  if (!validate()) return;
  const r = await fetch("/api/label/hint", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(payload()) });
  const d = await r.json();
  if (r.ok) { toast(`✓ hint sent (${d.queued}) — run: botctl label-hints`); }
  else toast("hint failed: " + (d.detail || r.status), false);
};
$("commit").onclick = async () => {
  if (!validate()) return;
  const body = { ...payload(), mode: $("mode").value };
  const r = await fetch("/api/label/commit", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body) });
  const d = await r.json();
  if (r.ok) { toast(`✓ committed ${d.region_count} region(s) [${d.mode}, ${d.frame_source}]\n` +
      (d.crops_written || []).join("\n")); boxes = []; renderBatch(); onScreenChange(); }
  else toast("commit failed: " + (d.detail || r.status), false);
};
$("clearBatch").onclick = () => { boxes = []; renderBatch(); redraw(); };
$("refresh").onclick = () => loadFrame(true);
$("grid").onchange = redraw;
$("inst").onchange = () => loadFrame(true);
$("scope").onchange = reloadScreensForScope;
$("screen").onchange = onScreenChange;
init();
</script>
</body>
</html>
"""
