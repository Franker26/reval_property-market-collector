"""
GET /ops/summary  — datos agregados para el dashboard operativo
GET /ops/dashboard — dashboard HTML autocontenido con auto-refresh
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import case, func, select

from app.core.rate_limiter import get_all_limiter_states
from app.db.models import CollectionError, CollectionRun, ZonapropSegmentScanQueue
from app.db.session import get_async_session_factory
from app.repositories import collection_errors as errors_repo
from app.repositories import collection_runs as runs_repo

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/summary")
async def ops_summary():
    """Datos agregados del pipeline para el dashboard operativo."""
    factory = get_async_session_factory()
    now = datetime.now(timezone.utc)

    async with factory() as session:
        # Último run (cualquier tipo)
        last_run = await runs_repo.get_last_completed(session)

        # Último url_discovery completado
        last_url_run = await runs_repo.get_last_completed(session, run_type="url_discovery_window")
        if last_url_run is None:
            last_url_run = await runs_repo.get_last_completed(session, run_type="url_discovery")

        # Performance del último ciclo: métricas agregadas de segment_runs completados
        perf_result = await session.execute(
            select(
                func.sum(ZonapropSegmentScanQueue.listings_found).label("urls_discovered"),
                func.sum(ZonapropSegmentScanQueue.new_count).label("urls_new"),
                func.sum(ZonapropSegmentScanQueue.changed_count).label("urls_changed"),
                func.sum(ZonapropSegmentScanQueue.requests_total).label("requests_total"),
                func.sum(ZonapropSegmentScanQueue.requests_success).label("requests_success"),
                func.sum(ZonapropSegmentScanQueue.requests_failed).label("requests_failed"),
                func.avg(ZonapropSegmentScanQueue.avg_latency_ms).label("avg_latency_ms"),
                func.max(ZonapropSegmentScanQueue.max_latency_ms).label("max_latency_ms"),
                func.sum(
                    case((ZonapropSegmentScanQueue.cooldown_triggered == True, 1), else_=0)  # noqa: E712
                ).label("cooldown_count"),
            )
            .where(
                ZonapropSegmentScanQueue.status == "complete",
                ZonapropSegmentScanQueue.completed_at >= now - timedelta(days=1),
            )
        )
        perf = perf_result.one()

        req_total = int(perf.requests_total or 0)
        req_success = int(perf.requests_success or 0)
        success_rate = round(req_success / req_total * 100, 1) if req_total > 0 else None

        # Errores últimas 24h por tipo
        errors_24h = await errors_repo.count_by_type_since(session, since=now - timedelta(hours=24))
        recent_errors_list = await errors_repo.list_recent(session, limit=10)

        # Segmentos problemáticos: fallidos o con muchos errores HTTP
        problematic_result = await session.execute(
            select(ZonapropSegmentScanQueue)
            .where(
                ZonapropSegmentScanQueue.status == "failed",
            )
            .order_by(ZonapropSegmentScanQueue.updated_at.desc())
            .limit(10)
        )
        problematic_segs = problematic_result.scalars().all()

        # Cooldowns hoy
        cooldown_today = await session.execute(
            select(func.count())
            .select_from(ZonapropSegmentScanQueue)
            .where(
                ZonapropSegmentScanQueue.cooldown_triggered == True,  # noqa: E712
                ZonapropSegmentScanQueue.completed_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
            )
        )
        cooldowns_today = cooldown_today.scalar_one() or 0

        # Trends: últimos 7 días de urls_discovered y errores por día
        _seg_day = func.date_trunc("day", ZonapropSegmentScanQueue.completed_at)
        trends_url_result = await session.execute(
            select(_seg_day.label("day"), func.sum(ZonapropSegmentScanQueue.listings_found).label("total"))
            .where(
                ZonapropSegmentScanQueue.status == "complete",
                ZonapropSegmentScanQueue.completed_at >= now - timedelta(days=7),
            )
            .group_by(_seg_day)
            .order_by(_seg_day)
        )
        daily_urls = [
            {"day": row.day.strftime("%Y-%m-%d"), "total": int(row.total or 0)}
            for row in trends_url_result
        ]

        _err_day = func.date_trunc("day", CollectionError.failed_at)
        trends_err_result = await session.execute(
            select(_err_day.label("day"), func.count().label("total"))
            .where(CollectionError.failed_at >= now - timedelta(days=7))
            .group_by(_err_day)
            .order_by(_err_day)
        )
        daily_errors = [
            {"day": row.day.strftime("%Y-%m-%d"), "total": int(row.total or 0)}
            for row in trends_err_result
        ]

        trends_lat_result = await session.execute(
            select(_seg_day.label("day"), func.avg(ZonapropSegmentScanQueue.avg_latency_ms).label("avg_ms"))
            .where(
                ZonapropSegmentScanQueue.status == "complete",
                ZonapropSegmentScanQueue.completed_at >= now - timedelta(days=7),
                ZonapropSegmentScanQueue.avg_latency_ms.isnot(None),
            )
            .group_by(_seg_day)
            .order_by(_seg_day)
        )
        daily_latency = [
            {"day": row.day.strftime("%Y-%m-%d"), "avg_ms": round(float(row.avg_ms), 1) if row.avg_ms else None}
            for row in trends_lat_result
        ]

    def _run_dict(r):
        if r is None:
            return None
        return {
            "id": r.id,
            "run_type": r.run_type,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_seconds": float(r.duration_seconds) if r.duration_seconds else None,
            "stats": r.stats_json,
        }

    def _err_dict(e):
        return {
            "id": e.id,
            "error_type": e.error_type,
            "http_status": e.http_status,
            "message": e.error_message,
            "retryable": e.retryable,
            "failed_at": e.failed_at.isoformat() if e.failed_at else None,
        }

    def _seg_dict(s):
        return {
            "id": s.id,
            "segment_id": s.segment_id,
            "status": s.status,
            "attempt_count": s.attempt_count,
            "last_error": s.last_error,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }

    return {
        "timestamp": now.isoformat(),
        "last_run": _run_dict(last_run),
        "performance": {
            "last_24h": {
                "urls_discovered": int(perf.urls_discovered or 0),
                "urls_new": int(perf.urls_new or 0),
                "urls_changed": int(perf.urls_changed or 0),
                "requests_total": req_total,
                "requests_success": req_success,
                "requests_failed": int(perf.requests_failed or 0),
                "success_rate_pct": success_rate,
                "avg_latency_ms": round(float(perf.avg_latency_ms), 1) if perf.avg_latency_ms else None,
                "max_latency_ms": round(float(perf.max_latency_ms), 1) if perf.max_latency_ms else None,
            }
        },
        "errors": {
            "last_24h_by_type": errors_24h,
            "total_last_24h": sum(errors_24h.values()),
            "recent": [_err_dict(e) for e in recent_errors_list],
            "problematic_segments": [_seg_dict(s) for s in problematic_segs],
        },
        "protection": {
            "cooldowns_today": cooldowns_today,
            "rate_limiter_states": get_all_limiter_states(),
        },
        "trends": {
            "daily_urls_discovered": daily_urls,
            "daily_errors": daily_errors,
            "daily_avg_latency_ms": daily_latency,
        },
    }


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reval MI — Dashboard Operativo</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #e2e8f0; min-height: 100vh; }

  header { background: #161b22; border-bottom: 1px solid #30363d; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
  .header-title { font-size: 16px; font-weight: 700; color: #58a6ff; }
  .header-right { display: flex; gap: 16px; align-items: center; font-size: 12px; color: #8b949e; }

  .global-badge { padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; letter-spacing: 0.05em; }
  .global-badge.ok  { background: #1f4b2e; color: #3fb950; }
  .global-badge.warn { background: #4b3000; color: #d29922; }
  .global-badge.err { background: #4b1c20; color: #f85149; }

  main { padding: 20px 24px; max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }

  /* ── Service card ── */
  .svc-card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; overflow: hidden; transition: border-color 0.3s; }
  .svc-card.running { border-color: #388bfd; }

  .svc-header { padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #21262d; }
  .svc-title-row { display: flex; align-items: center; gap: 10px; }
  .svc-title { font-size: 15px; font-weight: 700; color: #e6edf3; }
  .run-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .run-dot.running  { background: #3fb950; animation: pulse 1.5s infinite; }
  .run-dot.stopping { background: #d29922; animation: pulse 1s infinite; }
  .run-dot.idle     { background: #484f58; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
  .run-status { font-size: 12px; color: #8b949e; }
  .run-status.running  { color: #3fb950; font-weight: 600; }
  .run-status.stopping { color: #d29922; font-weight: 600; }

  .svc-body { padding: 16px 20px; }

  /* Metrics */
  .metrics-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  .chip { background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 8px 14px; min-width: 90px; }
  .chip-label { font-size: 11px; color: #8b949e; margin-bottom: 3px; }
  .chip-value { font-size: 17px; font-weight: 700; color: #e6edf3; }
  .chip-value.good  { color: #3fb950; }
  .chip-value.warn  { color: #d29922; }
  .chip-value.bad   { color: #f85149; }
  .chip-value.sm    { font-size: 13px; }

  /* Progress */
  .prog-wrap { margin-bottom: 14px; }
  .prog-bar  { height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; }
  .prog-fill { height: 100%; border-radius: 3px; background: #388bfd; transition: width .5s; }
  .prog-label { display: flex; justify-content: space-between; font-size: 11px; color: #8b949e; margin-top: 4px; }

  /* Buttons */
  .actions-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
  .btn { padding: 7px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; border: 1px solid transparent; transition: all .15s; display: inline-flex; align-items: center; gap: 5px; }
  .btn:disabled { opacity: .35; cursor: not-allowed; }
  .btn-run  { background: #1f4b2e; border-color: #2ea043; color: #3fb950; }
  .btn-run:not(:disabled):hover { background: #2ea043; color: #fff; }
  .btn-stop { background: #4b1c20; border-color: #f85149; color: #f85149; }
  .btn-stop:not(:disabled):hover { background: #7a1c20; }
  .btn-ghost { background: transparent; border-color: #30363d; color: #8b949e; }
  .btn-ghost:not(:disabled):hover { border-color: #8b949e; color: #e6edf3; }

  /* Schedule toggle button */
  .sched-btn { display: flex; align-items: center; gap: 7px; padding: 7px 14px; border-radius: 6px; border: 1px solid #30363d; background: #21262d; font-size: 12px; cursor: pointer; transition: all .15s; color: #e6edf3; }
  .sched-btn:hover { border-color: #58a6ff; }
  .sched-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .sched-dot.on  { background: #3fb950; }
  .sched-dot.off { background: #484f58; }
  .sched-next { color: #8b949e; font-size: 11px; }

  /* Log panel */
  .log-section { border-top: 1px solid #21262d; padding-top: 12px; }
  .log-header { display: flex; align-items: center; gap: 10px; }
  .log-toggle { background: none; border: none; color: #58a6ff; cursor: pointer; font-size: 12px; font-weight: 600; padding: 2px 0; }
  .log-toggle:hover { color: #79c0ff; }
  .log-refresh { background: none; border: none; color: #484f58; cursor: pointer; font-size: 13px; padding: 2px 6px; border-radius: 4px; }
  .log-refresh:hover { color: #8b949e; background: #21262d; }
  .log-box { display: none; margin-top: 10px; background: #0d1117; border: 1px solid #21262d; border-radius: 6px; max-height: 280px; overflow-y: auto; font-family: 'Consolas','Courier New',monospace; font-size: 11.5px; }
  .log-box.open { display: block; }
  .log-entry { padding: 3px 12px; border-bottom: 1px solid #161b22; display: flex; gap: 8px; align-items: baseline; }
  .log-entry:last-child { border-bottom: none; }
  .log-time { color: #484f58; white-space: nowrap; flex-shrink: 0; }
  .log-lvl  { width: 46px; font-weight: 700; white-space: nowrap; flex-shrink: 0; }
  .log-lvl.INFO    { color: #58a6ff; }
  .log-lvl.WARNING { color: #d29922; }
  .log-lvl.ERROR   { color: #f85149; }
  .log-lvl.DEBUG   { color: #484f58; }
  .log-msg { color: #c9d1d9; word-break: break-word; }
  .log-empty { padding: 14px; color: #484f58; text-align: center; font-size: 12px; }

  /* Global section */
  .global-card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; overflow: hidden; }
  .global-card-header { padding: 12px 20px; border-bottom: 1px solid #21262d; font-size: 12px; font-weight: 700; color: #8b949e; text-transform: uppercase; letter-spacing: .05em; }
  .global-card-body { padding: 14px 20px; }
  .global-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; }
  @media (max-width: 800px) { .global-grid { grid-template-columns: 1fr; } }
  .rl-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid #21262d; font-size: 12px; }
  .rl-row:last-child { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 10px; font-weight: 700; }
  .badge.ok       { background: #1f4b2e; color: #3fb950; }
  .badge.cooldown { background: #3b1c4b; color: #d2a8ff; }
  .badge.warn     { background: #4b3000; color: #d29922; }
  .badge.err      { background: #4b1c20; color: #f85149; }
  .sparkline { width: 100%; height: 36px; }
  .spark-label { font-size: 11px; color: #8b949e; margin-bottom: 6px; }

  /* Toast */
  #toast { position: fixed; bottom: 24px; right: 24px; padding: 11px 18px; border-radius: 8px; font-size: 13px; font-weight: 600; opacity: 0; transition: opacity .3s; pointer-events: none; max-width: 340px; z-index: 9999; }
  #toast.show { opacity: 1; }
  #toast.success { background: #1f4b2e; border: 1px solid #3fb950; color: #3fb950; }
  #toast.error   { background: #4b1c20; border: 1px solid #f85149; color: #f85149; }
  #toast.info    { background: #1c3a5e; border: 1px solid #388bfd; color: #58a6ff; }
</style>
</head>
<body>
<header>
  <div class="header-title">Reval MI — Dashboard Operativo</div>
  <div class="header-right">
    <span id="last-updated">Cargando...</span>
    <span id="global-badge" class="global-badge">—</span>
  </div>
</header>
<main>
  <div id="card-segment-discovery" class="svc-card"></div>
  <div id="card-url-discovery"      class="svc-card"></div>
  <div id="card-incremental-monitor" class="svc-card"></div>
  <div class="global-card">
    <div class="global-card-header">Métricas globales</div>
    <div class="global-card-body">
      <div class="global-grid">
        <div id="glob-errors"></div>
        <div id="glob-rl"></div>
        <div id="glob-trends"></div>
      </div>
    </div>
  </div>
</main>
<div id="toast"></div>
<script>
// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtNum(n) { return n == null ? '—' : Number(n).toLocaleString('es-AR'); }
function fmtPct(n) { return n == null ? '—' : Number(n).toFixed(1) + '%'; }
function fmtMs(n)  { return n == null ? '—' : Math.round(n) + ' ms'; }
function fmtDur(s) {
  if (s == null) return '—';
  if (s < 60)   return Math.round(s) + 's';
  if (s < 3600) return (s/60).toFixed(1) + 'min';
  return (s/3600).toFixed(1) + 'h';
}
function fmtAgo(ts) {
  if (!ts) return '—';
  var d = (Date.now() - new Date(ts)) / 1000;
  if (d < 60)    return 'hace ' + Math.round(d) + 's';
  if (d < 3600)  return 'hace ' + Math.round(d/60) + 'min';
  if (d < 86400) return 'hace ' + (d/3600).toFixed(1) + 'h';
  return 'hace ' + Math.round(d/86400) + 'd';
}
function fmtLocalDow(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString('es-AR',{weekday:'short',hour:'2-digit',minute:'2-digit'});
}
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
// safe deep-get: safe(obj, 'a', 'b') === obj.a.b (returns undefined if any key is missing)
function safe(obj) {
  var cur = obj;
  for (var i = 1; i < arguments.length; i++) {
    if (cur == null) return undefined;
    cur = cur[arguments[i]];
  }
  return cur;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type) {
  var el = document.getElementById('toast');
  el.textContent = msg; el.className = (type || 'info') + ' show';
  setTimeout(function() { el.className = ''; }, 4000);
}

// ── API ───────────────────────────────────────────────────────────────────────
function api(method, url, body) {
  return fetch(url, {
    method: method,
    headers: {'Content-Type':'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  }).then(function(r) {
    return r.json().then(function(d) {
      if (!r.ok) throw new Error(d.detail || JSON.stringify(d));
      return d;
    });
  });
}

// ── State ─────────────────────────────────────────────────────────────────────
var _data = {};
var _loading = {};
var _logStates = {};

// Modo por servicio: 'auto' | 'manual'. Persiste en localStorage.
var _MODE_KEY = 'reval_svc_mode';
function _loadModes() {
  try { return JSON.parse(localStorage.getItem(_MODE_KEY) || '{}'); } catch(e) { return {}; }
}
function _saveMode(svcKey, mode) {
  var m = _loadModes(); m[svcKey] = mode; localStorage.setItem(_MODE_KEY, JSON.stringify(m));
}
function getMode(svcKey) { return _loadModes()[svcKey] || 'auto'; }

function saveLogState(cardId) {
  var box = document.querySelector('#card-' + cardId + ' .log-box');
  _logStates[cardId] = {
    open: !!(box && box.classList.contains('open')),
    content: box ? box.innerHTML : '',
  };
}
function restoreLogState(cardId) {
  var s = _logStates[cardId];
  if (!s || !s.open) return;
  var box = document.querySelector('#card-' + cardId + ' .log-box');
  var btn = document.querySelector('#card-' + cardId + ' .log-toggle');
  if (box) { box.classList.add('open'); if (s.content) box.innerHTML = s.content; }
  if (btn) btn.textContent = '▼ Ocultar logs';
}

// ── Actions ───────────────────────────────────────────────────────────────────
function triggerRun(svcKey, endpoint) {
  if (_loading[svcKey]) return;
  _loading[svcKey] = true; renderAll();
  function done() { _loading[svcKey] = false; renderAll(); }
  api('POST', endpoint, {}).then(function() {
    toast(svcKey + ' iniciado', 'success');
    setTimeout(refresh, 1500);
    done();
  }, function(e) {
    toast('Error: ' + e.message, 'error');
    done();
  });
}

function cancelRun(svcKey) {
  if (!confirm('Cancelar ' + svcKey + '? El run se detendra en el proximo punto seguro.')) return;
  api('POST', '/ops/cancel/' + svcKey, {}).then(function() {
    toast('Cancelacion solicitada', 'info');
    setTimeout(refresh, 2000);
  }).catch(function(e) { toast('Error: ' + e.message, 'error'); });
}

function toggleSchedule(jobId, isPaused) {
  if (!isPaused && !confirm('Pausar el schedule de ' + jobId + '?')) return;
  var action = isPaused ? 'resume-job' : 'pause-job';
  api('POST', '/discovery/scheduler/' + action + '/' + jobId, {}).then(function() {
    toast(isPaused ? 'Schedule activado' : 'Schedule pausado', 'info');
    setTimeout(refresh, 600);
  }).catch(function(e) { toast('Error: ' + e.message, 'error'); });
}

// Mapa de service key → job IDs del scheduler (evita pasar arrays en onclick)
var _SCHED_JOBS = {
  'segment_discovery': ['weekly_segment_discovery'],
  'url_discovery':     ['weekday_url_discovery', 'sunday_url_discovery'],
  'incremental_monitor': [],
};

// ── Mode toggle ───────────────────────────────────────────────────────────────
function setMode(svcKey, newMode) {
  if (newMode === getMode(svcKey)) return;
  _saveMode(svcKey, newMode);
  var action = newMode === 'manual' ? 'pause-job' : 'resume-job';
  var jobs = _SCHED_JOBS[svcKey] || [];
  var promises = jobs.map(function(jid) {
    return api('POST', '/discovery/scheduler/' + action + '/' + jid, {}).catch(function(){});
  });
  Promise.all(promises).then(function() {
    toast(newMode === 'manual' ? 'Modo manual: scheduler pausado' : 'Modo auto: scheduler activado', 'info');
    setTimeout(refresh, 600);
  });
}

function modeToggle(svcKey) {
  var cur = getMode(svcKey);
  return '<div style="display:flex;align-items:center;gap:6px;border:1px solid #30363d;border-radius:6px;padding:3px 4px;background:#0d1117">'
    + '<button class="btn ' + (cur === 'auto' ? 'btn-run' : 'btn-ghost') + '" style="padding:4px 10px;font-size:11px" '
    + 'onclick="setMode(&#39;' + svcKey + '&#39;,&#39;auto&#39;)">AUTO</button>'
    + '<button class="btn ' + (cur === 'manual' ? 'btn-run' : 'btn-ghost') + '" style="padding:4px 10px;font-size:11px" '
    + 'onclick="setMode(&#39;' + svcKey + '&#39;,&#39;manual&#39;)">MANUAL</button>'
    + '</div>';
}

// ── Logs ──────────────────────────────────────────────────────────────────────
function toggleLogs(cardId, keyword) {
  var box = document.querySelector('#card-' + cardId + ' .log-box');
  var btn = document.querySelector('#card-' + cardId + ' .log-toggle');
  if (!box) return;
  if (box.classList.contains('open')) {
    box.classList.remove('open');
    if (btn) btn.textContent = '► Ver logs';
    _logStates[cardId] = { open: false, content: '' };
  } else {
    box.classList.add('open');
    if (btn) btn.textContent = '▼ Ocultar logs';
    loadLogs(cardId, keyword);
  }
}

function loadLogs(cardId, keyword) {
  var box = document.querySelector('#card-' + cardId + ' .log-box');
  if (!box) return;
  box.innerHTML = '<div class="log-empty">Cargando...</div>';
  fetch('/logs?limit=200&logger=app').then(function(r) { return r.json(); }).then(function(d) {
    var kw = (keyword || '').toLowerCase();
    var entries = (d.entries || []).filter(function(e) {
      return !kw || (e.message || '').toLowerCase().indexOf(kw) >= 0;
    }).slice(-80);
    if (!entries.length) { box.innerHTML = '<div class="log-empty">Sin logs para este servicio</div>'; return; }
    box.innerHTML = entries.map(function(e) {
      var t = e.time ? new Date(e.time).toLocaleTimeString('es-AR') : '';
      var lv = e.level || 'INFO';
      return '<div class="log-entry">'
        + '<span class="log-time">' + t + '</span>'
        + '<span class="log-lvl ' + lv + '">' + lv + '</span>'
        + '<span class="log-msg">' + esc(e.message || '') + '</span>'
        + '</div>';
    }).join('');
    box.scrollTop = box.scrollHeight;
    _logStates[cardId] = { open: true, content: box.innerHTML };
  }).catch(function() { box.innerHTML = '<div class="log-empty">Error cargando logs</div>'; });
}

// ── Component builders ────────────────────────────────────────────────────────
function chip(label, value, cls) {
  return '<div class="chip"><div class="chip-label">' + label + '</div>'
    + '<div class="chip-value' + (cls ? ' ' + cls : '') + '">' + value + '</div></div>';
}

function schedBtn(jobId, jobs) {
  var j = null;
  for (var i = 0; i < (jobs || []).length; i++) { if (jobs[i].id === jobId) { j = jobs[i]; break; } }
  if (!j) return '';
  var paused = j.paused;
  var nextStr = paused ? 'Pausado' : fmtLocalDow(j.next_run);
  return '<button class="sched-btn" onclick="toggleSchedule(&#39;' + jobId + '&#39;,' + paused + ')">'
    + '<span class="sched-dot ' + (paused ? 'off' : 'on') + '"></span>'
    + '<span>' + (paused ? 'PAUSADO' : 'ACTIVO') + '</span>'
    + '<span class="sched-next">| ' + nextStr + '</span>'
    + '</button>';
}

function buildCard(id, title, isRunning, durationS, progressHtml, chipsHtml, actionsHtml, logKeyword, isStopping) {
  var statusLabel = isRunning
    ? (isStopping ? 'STOPPING... ' : 'RUNNING ') + fmtDur(durationS)
    : 'IDLE';
  var statusCls = isRunning ? (isStopping ? ' stopping' : ' running') : '';
  return '<div class="svc-header">'
    + '<div class="svc-title-row">'
    + '<span class="run-dot ' + (isRunning ? (isStopping ? 'stopping' : 'running') : 'idle') + '"></span>'
    + '<span class="svc-title">' + title + '</span>'
    + '<span class="run-status' + statusCls + '">' + statusLabel + '</span>'
    + '</div></div>'
    + '<div class="svc-body">'
    + (progressHtml || '')
    + '<div class="metrics-row">' + chipsHtml + '</div>'
    + '<div class="actions-row">' + actionsHtml + '</div>'
    + '<div class="log-section">'
    + '<div class="log-header">'
    + '<button class="log-toggle" onclick="toggleLogs(&#39;' + id + '&#39;,&#39;' + logKeyword + '&#39;)">► Ver logs</button>'
    + '<button class="log-refresh" onclick="loadLogs(&#39;' + id + '&#39;,&#39;' + logKeyword + '&#39;)" title="Refrescar">&#8635;</button>'
    + '</div>'
    + '<div class="log-box"></div>'
    + '</div></div>';
}

// ── Main render ───────────────────────────────────────────────────────────────
function renderAll() {
  var h   = _data.health || {};
  var s   = _data.summary || {};
  var perf = (s.performance || {}).last_24h || {};
  var sp  = h.segment_progress || {};
  var sd  = h.segment_discovery || {};
  var jobs = h.scheduler || [];
  var ar  = h.active_run || null;
  var lbt = h.last_completed_by_type || {};
  var arType    = ar ? ar.run_type : null;
  var arDur     = ar ? ar.duration_so_far_s : null;
  var arStopping = ar ? !!ar.cancel_requested : false;

  // ── Helper: next run info block ──────────────────────────────────────────
  function nextRunBlock(jobIds) {
    var lines = [];
    for (var i = 0; i < jobIds.length; i++) {
      var jid = jobIds[i];
      for (var k = 0; k < jobs.length; k++) {
        if (jobs[k].id === jid) {
          var label = jid === 'weekly_segment_discovery' ? 'Sáb'
                    : jid === 'weekday_url_discovery'    ? 'L-V'
                    : jid === 'sunday_url_discovery'     ? 'Dom' : jid;
          var next = jobs[k].next_run ? fmtLocalDow(jobs[k].next_run) : 'Pausado';
          lines.push('<span style="font-size:11px;color:#8b949e">' + label + ': ' + next + '</span>');
          break;
        }
      }
    }
    return lines.length
      ? '<div style="display:flex;gap:12px;align-items:center">' + lines.join('') + '</div>'
      : '';
  }

  // ── Segment Discovery ────────────────────────────────────────────────────
  (function() {
    var id = 'segment-discovery';
    var svcKey = 'segment_discovery';
    var schedIds = ['weekly_segment_discovery'];
    var mode = getMode(svcKey);
    var running = arType === 'segment_discovery';
    saveLogState(id);
    var lsd = lbt.segment_discovery || null;
    var lsdStatus = lsd ? (lsd.status || '') : '';
    var chips = [
      chip('Hojas', fmtNum(sd.leaves), 'good'),
      chip('Oversized', fmtNum(sd.oversized), sd.oversized > 0 ? 'warn' : ''),
      chip('Total segs.', fmtNum(sd.segments_total)),
      chip('Ultimo', lsd ? fmtAgo(lsd.finished_at) : '—'),
      chip('Estado ant.', lsdStatus || '—',
        lsdStatus === 'success' ? 'good' : lsdStatus === 'failed' ? 'bad' : 'sm'),
    ].join('');
    var ld = _loading[svcKey];
    var execBtn = mode === 'manual'
      ? '<button class="btn btn-run" onclick="triggerRun(&#39;' + svcKey + '&#39;,&#39;/discovery/segment-discovery&#39;)"'
          + (ld ? ' disabled' : '') + '>' + (ld ? 'Iniciando...' : '► Ejecutar') + '</button>'
      : nextRunBlock(schedIds);
    var acts = [
      modeToggle(svcKey),
      execBtn,
      '<button class="btn btn-stop" onclick="cancelRun(&#39;' + svcKey + '&#39;)"'
        + (!running ? ' disabled' : '') + '>■ Cancelar</button>',
    ].join('');
    var el = document.getElementById('card-' + id);
    el.className = 'svc-card' + (running ? ' running' : '');
    el.innerHTML = buildCard(id, 'Segment Discovery', running, arDur, '', chips, acts, 'segment_discovery', running && arStopping);
    restoreLogState(id);
  })();

  // ── URL Discovery ────────────────────────────────────────────────────────
  (function() {
    var id = 'url-discovery';
    var svcKey = 'url_discovery';
    var schedIds = ['weekday_url_discovery', 'sunday_url_discovery'];
    var mode = getMode(svcKey);
    var running = arType === 'url_discovery_window';
    saveLogState(id);
    var total = sp.total || 0, complete = sp.complete || 0;
    var pct = total > 0 ? Math.round(complete / total * 100) : 0;
    var progHtml = total > 0
      ? '<div class="prog-wrap">'
        + '<div class="prog-bar"><div class="prog-fill" style="width:' + pct + '%"></div></div>'
        + '<div class="prog-label">'
        + '<span>Segmentos: ' + complete + '/' + total + ' (' + pct + '%)</span>'
        + (sp.failed > 0 ? '<span style="color:#f85149">' + sp.failed + ' fallidos</span>' : '<span></span>')
        + '</div></div>'
      : '';
    var lud = lbt.url_discovery || null;
    var chips = [
      chip('URLs desc.', fmtNum(perf.urls_discovered)),
      chip('Nuevas', fmtNum(perf.urls_new), 'good'),
      chip('Modificadas', fmtNum(perf.urls_changed), perf.urls_changed > 0 ? 'warn' : ''),
      chip('Exito req.', fmtPct(perf.success_rate_pct), (perf.success_rate_pct||100) >= 90 ? 'good' : 'bad'),
      chip('Latencia', fmtMs(perf.avg_latency_ms)),
      chip('Ultimo', lud ? fmtAgo(lud.finished_at) : '—'),
    ].join('');
    var ld = _loading[svcKey];
    var execBtn = mode === 'manual'
      ? '<button class="btn btn-run" onclick="triggerRun(&#39;' + svcKey + '&#39;,&#39;/discovery/url-discovery&#39;)"'
          + (ld ? ' disabled' : '') + '>' + (ld ? 'Iniciando...' : '► Ejecutar') + '</button>'
      : nextRunBlock(schedIds);
    var acts = [
      modeToggle(svcKey),
      execBtn,
      '<button class="btn btn-stop" onclick="cancelRun(&#39;' + svcKey + '&#39;)"'
        + (!running ? ' disabled' : '') + '>■ Cancelar</button>',
    ].join('');
    var el = document.getElementById('card-' + id);
    el.className = 'svc-card' + (running ? ' running' : '');
    el.innerHTML = buildCard(id, 'URL Discovery', running, arDur, progHtml, chips, acts, 'url_discovery', running && arStopping);
    restoreLogState(id);
  })();

  // ── Incremental Monitor ──────────────────────────────────────────────────
  (function() {
    var id = 'incremental-monitor';
    var svcKey = 'incremental_monitor';
    var running = arType === 'incremental_monitor';
    saveLogState(id);
    var last = lbt.incremental_monitor || null;
    var lastStatus = last ? (last.status || '') : '';
    var lastStats = last ? (last.stats || {}) : {};
    var chips = [
      chip('Ultimo', last ? fmtAgo(last.finished_at) : '—'),
      chip('Estado ant.', lastStatus || '—',
        lastStatus === 'success' ? 'good' : lastStatus === 'failed' ? 'bad' : 'sm'),
      chip('Duracion ant.', last ? fmtDur(last.duration_seconds) : '—'),
      chip('Found', lastStats.listings_found != null ? fmtNum(lastStats.listings_found) : '—'),
    ].join('');
    var ld = _loading[svcKey];
    var acts = [
      '<button class="btn btn-run" onclick="triggerRun(&#39;' + svcKey + '&#39;,&#39;/discovery/incremental-monitor&#39;)"'
        + (ld ? ' disabled' : '') + '>' + (ld ? 'Iniciando...' : '► Ejecutar') + '</button>',
      '<button class="btn btn-stop" onclick="cancelRun(&#39;' + svcKey + '&#39;)"'
        + (!running ? ' disabled' : '') + '>■ Cancelar</button>',
    ].join('');
    var el = document.getElementById('card-' + id);
    el.className = 'svc-card' + (running ? ' running' : '');
    el.innerHTML = buildCard(id, 'Incremental Monitor', running, arDur, '', chips, acts, 'incremental_monitor', running && arStopping);
    restoreLogState(id);
  })();

  // ── Global section ───────────────────────────────────────────────────────
  (function() {
    var errByType = (s.errors ? s.errors.last_24h_by_type : null) || {};
    var errEntries = Object.keys(errByType).map(function(k) { return [k, errByType[k]]; });
    errEntries.sort(function(a,b) { return b[1]-a[1]; });
    document.getElementById('glob-errors').innerHTML =
      '<div class="spark-label">Errores 24h por tipo</div>'
      + (errEntries.length
        ? errEntries.map(function(kv) {
            return '<div class="rl-row"><span>' + kv[0] + '</span>'
              + '<span style="color:#f85149;font-weight:700">' + kv[1] + '</span></div>';
          }).join('')
        : '<div style="color:#484f58;font-size:12px">Sin errores en 24h</div>');

    var prot = s.protection || {};
    var rl = prot.rate_limiter_states || {};
    var rlKeys = Object.keys(rl);
    document.getElementById('glob-rl').innerHTML =
      '<div class="spark-label">Rate limiters</div>'
      + (rlKeys.length
        ? rlKeys.map(function(k) {
            var v = rl[k];
            return '<div class="rl-row"><span style="font-size:12px">' + k + '</span><span>'
              + (v.in_cooldown
                ? '<span class="badge cooldown">COOLDOWN ' + v.cooldown_remaining_s + 's</span>'
                : '<span class="badge ok">OK</span>')
              + ' <span style="color:#484f58;font-size:11px">err:' + v.errors_in_window + '</span>'
              + '</span></div>';
          }).join('')
        : '<div style="color:#484f58;font-size:12px">Sin limiters activos</div>');

    var trends = s.trends || {};
    var urlData = trends.daily_urls_discovered || [];
    document.getElementById('glob-trends').innerHTML =
      '<div class="spark-label">URLs / dia (7d)</div>' + sparkline(urlData, 'total');
  })();

  // ── Global badge ─────────────────────────────────────────────────────────
  var badge = document.getElementById('global-badge');
  var hRE = h.recent_errors || {};
  var err1h = hRE.total_last_1h || 0;
  var prot2 = (s.protection || {}).rate_limiter_states || {};
  var cooldown = Object.keys(prot2).some(function(k) { return prot2[k].in_cooldown; });
  if (cooldown || err1h > 10) { badge.textContent = 'ALERTA'; badge.className = 'global-badge err'; }
  else if ((sp.failed || 0) > 0 || err1h > 2) { badge.textContent = 'ADVERTENCIA'; badge.className = 'global-badge warn'; }
  else { badge.textContent = 'OPERATIVO'; badge.className = 'global-badge ok'; }
}

// ── Sparkline ─────────────────────────────────────────────────────────────────
function sparkline(data, key) {
  if (!data || !data.length) return '<span style="color:#484f58;font-size:12px">Sin datos</span>';
  var vals = data.map(function(d) { return d[key] || 0; });
  var max = Math.max.apply(null, vals.concat([1]));
  var w = 280, h = 36, pad = 2;
  var pts = vals.map(function(v, i) {
    var x = pad + (i / ((vals.length-1)||1)) * (w-2*pad);
    var y = h - pad - (v/max) * (h-2*pad);
    return x + ',' + y;
  }).join(' ');
  return '<svg class="sparkline" viewBox="0 0 ' + w + ' ' + h + '">'
    + '<polyline points="' + pts + '" fill="none" stroke="#388bfd" stroke-width="2"/>'
    + '<text x="' + (w-pad) + '" y="' + h + '" text-anchor="end" font-size="9" fill="#484f58">'
    + vals[vals.length-1].toLocaleString('es-AR') + '</text></svg>';
}

// ── Data fetch ────────────────────────────────────────────────────────────────
function refresh() {
  Promise.all([
    fetch('/health/discovery').then(function(r) { return r.json(); }),
    fetch('/ops/summary').then(function(r) { return r.json(); }),
  ]).then(function(results) {
    _data = { health: results[0], summary: results[1] };
    renderAll();
    document.getElementById('last-updated').textContent =
      'Actualizado: ' + new Date().toLocaleTimeString('es-AR');
  }).catch(function(e) {
    var b = document.getElementById('global-badge');
    b.textContent = 'ERROR'; b.className = 'global-badge err';
    console.error('Dashboard error:', e);
  });
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


@router.post("/cancel/{run_type}")
async def cancel_run(run_type: str):
    """Solicita cancelación del run activo del tipo especificado."""
    valid_types = {"segment_discovery", "url_discovery", "incremental_monitor"}
    if run_type not in valid_types:
        raise HTTPException(400, f"run_type inválido: {run_type!r}. Usar: {sorted(valid_types)}")
    from app.services.discovery_service import request_cancel
    request_cancel(run_type)
    return {"status": "cancel_requested", "run_type": run_type}


@router.get("/dashboard", response_class=HTMLResponse)
async def ops_dashboard():
    """Dashboard operativo HTML con auto-refresh cada 30s."""
    return HTMLResponse(content=_DASHBOARD_HTML)
