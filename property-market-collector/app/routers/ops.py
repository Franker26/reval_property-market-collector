"""
GET /ops/summary  — datos agregados para el dashboard operativo
GET /ops/dashboard — dashboard HTML autocontenido con auto-refresh
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text

from app.core.rate_limiter import get_all_limiter_states
from app.db.models import CollectionError, CollectionRun, UrlDiscoverySegmentRun
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
                func.sum(UrlDiscoverySegmentRun.listings_found).label("urls_discovered"),
                func.sum(UrlDiscoverySegmentRun.new_count).label("urls_new"),
                func.sum(UrlDiscoverySegmentRun.changed_count).label("urls_changed"),
                func.sum(UrlDiscoverySegmentRun.requests_total).label("requests_total"),
                func.sum(UrlDiscoverySegmentRun.requests_success).label("requests_success"),
                func.sum(UrlDiscoverySegmentRun.requests_failed).label("requests_failed"),
                func.avg(UrlDiscoverySegmentRun.avg_latency_ms).label("avg_latency_ms"),
                func.max(UrlDiscoverySegmentRun.max_latency_ms).label("max_latency_ms"),
                func.sum(
                    func.cast(UrlDiscoverySegmentRun.cooldown_triggered, type_=None)
                ).label("cooldown_count"),
            )
            .where(
                UrlDiscoverySegmentRun.status == "complete",
                UrlDiscoverySegmentRun.completed_at >= now - timedelta(days=1),
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
            select(UrlDiscoverySegmentRun)
            .where(
                UrlDiscoverySegmentRun.status == "failed",
            )
            .order_by(UrlDiscoverySegmentRun.updated_at.desc())
            .limit(10)
        )
        problematic_segs = problematic_result.scalars().all()

        # Cooldowns hoy
        cooldown_today = await session.execute(
            select(func.count())
            .select_from(UrlDiscoverySegmentRun)
            .where(
                UrlDiscoverySegmentRun.cooldown_triggered == True,  # noqa: E712
                UrlDiscoverySegmentRun.completed_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
            )
        )
        cooldowns_today = cooldown_today.scalar_one() or 0

        # Trends: últimos 7 días de urls_discovered y errores por día
        trends_url_result = await session.execute(
            select(
                func.date_trunc("day", UrlDiscoverySegmentRun.completed_at).label("day"),
                func.sum(UrlDiscoverySegmentRun.listings_found).label("total"),
            )
            .where(
                UrlDiscoverySegmentRun.status == "complete",
                UrlDiscoverySegmentRun.completed_at >= now - timedelta(days=7),
            )
            .group_by(text("day"))
            .order_by(text("day"))
        )
        daily_urls = [
            {"day": row.day.strftime("%Y-%m-%d"), "total": int(row.total or 0)}
            for row in trends_url_result
        ]

        trends_err_result = await session.execute(
            select(
                func.date_trunc("day", CollectionError.failed_at).label("day"),
                func.count().label("total"),
            )
            .where(CollectionError.failed_at >= now - timedelta(days=7))
            .group_by(text("day"))
            .order_by(text("day"))
        )
        daily_errors = [
            {"day": row.day.strftime("%Y-%m-%d"), "total": int(row.total or 0)}
            for row in trends_err_result
        ]

        trends_lat_result = await session.execute(
            select(
                func.date_trunc("day", UrlDiscoverySegmentRun.completed_at).label("day"),
                func.avg(UrlDiscoverySegmentRun.avg_latency_ms).label("avg_ms"),
            )
            .where(
                UrlDiscoverySegmentRun.status == "complete",
                UrlDiscoverySegmentRun.completed_at >= now - timedelta(days=7),
                UrlDiscoverySegmentRun.avg_latency_ms.isnot(None),
            )
            .group_by(text("day"))
            .order_by(text("day"))
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
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 18px; font-weight: 600; color: #90cdf4; }
  #status-badge { font-size: 12px; padding: 4px 10px; border-radius: 12px; background: #2d3748; color: #a0aec0; }
  #status-badge.ok { background: #1a472a; color: #68d391; }
  #status-badge.warn { background: #744210; color: #f6ad55; }
  #status-badge.err { background: #742a2a; color: #fc8181; }
  main { padding: 24px; max-width: 1400px; margin: 0 auto; }
  .grid { display: grid; gap: 16px; }
  .grid-2 { grid-template-columns: repeat(2, 1fr); }
  .grid-3 { grid-template-columns: repeat(3, 1fr); }
  .grid-4 { grid-template-columns: repeat(4, 1fr); }
  @media (max-width: 900px) { .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; } }
  .card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 8px; padding: 20px; }
  .card h2 { font-size: 13px; font-weight: 600; color: #718096; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }
  .stat { margin-bottom: 12px; }
  .stat label { font-size: 12px; color: #718096; display: block; margin-bottom: 2px; }
  .stat .value { font-size: 22px; font-weight: 700; color: #e2e8f0; }
  .stat .value.good { color: #68d391; }
  .stat .value.warn { color: #f6ad55; }
  .stat .value.bad { color: #fc8181; }
  .stat .value.sm { font-size: 14px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge.success { background: #1a472a; color: #68d391; }
  .badge.failed { background: #742a2a; color: #fc8181; }
  .badge.running { background: #1a3a5c; color: #90cdf4; }
  .badge.partial { background: #744210; color: #f6ad55; }
  .badge.warning { background: #744210; color: #f6ad55; }
  .badge.cooldown { background: #4a1942; color: #d6bcfa; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px; color: #718096; font-weight: 600; border-bottom: 1px solid #2d3748; }
  td { padding: 8px; border-bottom: 1px solid #1a1d2e; color: #cbd5e0; }
  tr:last-child td { border-bottom: none; }
  .progress-bar { height: 6px; background: #2d3748; border-radius: 3px; overflow: hidden; margin-top: 4px; }
  .progress-bar-fill { height: 100%; background: #4299e1; border-radius: 3px; transition: width 0.5s; }
  .sparkline { width: 100%; height: 40px; }
  .section-title { font-size: 15px; font-weight: 600; color: #a0aec0; margin: 24px 0 12px; }
  .empty { color: #4a5568; font-size: 13px; font-style: italic; }
  #last-updated { font-size: 11px; color: #4a5568; }
  .rl-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #2d3748; font-size: 13px; }
  .rl-row:last-child { border-bottom: none; }
</style>
</head>
<body>
<header>
  <h1>Reval MI — Dashboard Operativo</h1>
  <div style="display:flex;gap:12px;align-items:center">
    <span id="last-updated">Cargando...</span>
    <span id="status-badge">—</span>
  </div>
</header>
<main>
  <div id="root"><p style="color:#718096;padding:40px 0">Cargando datos...</p></div>
</main>
<script>
const fmtNum = n => n == null ? '—' : n.toLocaleString('es-AR');
const fmtPct = n => n == null ? '—' : n.toFixed(1) + '%';
const fmtMs = n => n == null ? '—' : n.toFixed(0) + ' ms';
const fmtDur = s => {
  if (s == null) return '—';
  if (s < 60) return s.toFixed(0) + 's';
  if (s < 3600) return (s/60).toFixed(1) + 'min';
  return (s/3600).toFixed(2) + 'h';
};
const fmtTs = ts => ts ? new Date(ts).toLocaleString('es-AR') : '—';

function badge(status) {
  const cls = ['success','failed','running','partial','warning'].includes(status) ? status : '';
  return `<span class="badge ${cls}">${status}</span>`;
}

function sparkline(data, key) {
  if (!data || !data.length) return '<span class="empty">sin datos</span>';
  const vals = data.map(d => d[key] || 0);
  const max = Math.max(...vals, 1);
  const w = 200, h = 40, pad = 2;
  const pts = vals.map((v, i) => {
    const x = pad + (i / (vals.length - 1 || 1)) * (w - 2*pad);
    const y = h - pad - (v / max) * (h - 2*pad);
    return `${x},${y}`;
  }).join(' ');
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}">
    <polyline points="${pts}" fill="none" stroke="#4299e1" stroke-width="2"/>
    <text x="${w-pad}" y="${h}" text-anchor="end" font-size="9" fill="#4a5568">${vals[vals.length-1].toLocaleString()}</text>
  </svg>`;
}

function renderScheduler(jobs) {
  if (!jobs || !jobs.length) return '<span class="empty">sin datos</span>';
  return jobs.map(j => `
    <div class="rl-row">
      <span style="font-size:12px">${j.id}</span>
      <span>${j.paused
        ? '<span class="badge warning">PAUSADO</span>'
        : '<span class="badge success">ACTIVO</span>'}
        <span style="color:#718096;margin-left:8px;font-size:11px">${j.next_run ? 'próx: ' + new Date(j.next_run).toLocaleString('es-AR') : '—'}</span>
      </span>
    </div>`).join('');
}

function renderRateLimiters(rl) {
  if (!rl || !Object.keys(rl).length) return '<span class="empty">ninguno activo</span>';
  return Object.entries(rl).map(([k, v]) => `
    <div class="rl-row">
      <span>${k}</span>
      <span>${v.in_cooldown
        ? `<span class="badge cooldown">COOLDOWN ${v.cooldown_remaining_s}s</span>`
        : `<span class="badge success">OK</span>`}
        <span style="color:#718096;margin-left:8px;font-size:11px">err: ${v.errors_in_window} | req/min: ${v.requests_this_minute}</span>
      </span>
    </div>`).join('');
}

function renderErrors(errs) {
  if (!errs || !errs.length) return '<span class="empty">Sin errores recientes</span>';
  return `<table><thead><tr><th>Tipo</th><th>HTTP</th><th>Retry</th><th>Cuando</th><th>Mensaje</th></tr></thead><tbody>` +
    errs.map(e => `<tr>
      <td>${e.error_type}</td>
      <td>${e.http_status || '—'}</td>
      <td>${e.retryable ? '✓' : '✗'}</td>
      <td>${fmtTs(e.failed_at)}</td>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e.message || '—'}</td>
    </tr>`).join('') + '</tbody></table>';
}

function renderProblematic(segs) {
  if (!segs || !segs.length) return '<span class="empty">Sin segmentos problemáticos</span>';
  return `<table><thead><tr><th>Seg ID</th><th>Estado</th><th>Intentos</th><th>Último error</th></tr></thead><tbody>` +
    segs.map(s => `<tr>
      <td>${s.segment_id}</td>
      <td>${badge(s.status)}</td>
      <td>${s.attempt_count}</td>
      <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.last_error || '—'}</td>
    </tr>`).join('') + '</tbody></table>';
}

function renderErrByType(byType) {
  const entries = Object.entries(byType || {});
  if (!entries.length) return '<span class="empty">Sin errores en 24h</span>';
  return entries.sort((a,b) => b[1]-a[1]).map(([k,v]) =>
    `<div class="rl-row"><span>${k}</span><span style="color:#fc8181;font-weight:600">${v}</span></div>`
  ).join('');
}

function render(data) {
  const { last_run, performance, errors, protection, trends } = data;
  const perf = performance?.last_24h || {};
  const hasActiveRun = !!data.active_run;

  const root = document.getElementById('root');
  root.innerHTML = `
    <!-- Estado General -->
    <div class="section-title">Estado General</div>
    <div class="grid" style="grid-template-columns:repeat(5,1fr)">
      <div class="card">
        <h2>Último Run</h2>
        ${last_run ? `
          <div class="stat"><label>Tipo</label><div class="value sm">${last_run.run_type}</div></div>
          <div class="stat"><label>Estado</label><div>${badge(last_run.status)}</div></div>
          <div class="stat"><label>Duración</label><div class="value sm">${fmtDur(last_run.duration_seconds)}</div></div>
          <div class="stat"><label>Finalizado</label><div class="value sm">${fmtTs(last_run.finished_at)}</div></div>
        ` : '<span class="empty">Sin runs</span>'}
      </div>
      <div class="card">
        <h2>Run Activo</h2>
        ${hasActiveRun ? `
          <div class="stat"><label>Tipo</label><div class="value sm">${data.active_run.run_type}</div></div>
          <div class="stat"><label>Estado</label><div>${badge('running')}</div></div>
          <div class="stat"><label>Duración</label><div class="value sm">${fmtDur(data.active_run.duration_so_far_s)}</div></div>
        ` : '<div style="color:#68d391;font-size:13px;margin-top:8px">— En reposo —</div>'}
      </div>
      <div class="card">
        <h2>Progreso Segmentos</h2>
        ${data.segment_progress ? `
          <div class="stat">
            <label>Completados / Total</label>
            <div class="value">${fmtNum(data.segment_progress.complete)} / ${fmtNum(data.segment_progress.total)}</div>
            <div class="progress-bar"><div class="progress-bar-fill" style="width:${data.segment_progress.progress_pct || 0}%"></div></div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px;font-size:12px">
            <div><span style="color:#718096">Pendientes</span><br><strong>${data.segment_progress.pending}</strong></div>
            <div><span style="color:#718096">Fallidos</span><br><strong style="color:${data.segment_progress.failed > 0 ? '#fc8181' : '#68d391'}">${data.segment_progress.failed}</strong></div>
          </div>
        ` : '<span class="empty">Sin datos</span>'}
      </div>
      <div class="card">
        <h2>Errores Recientes</h2>
        <div class="stat"><label>Última hora</label><div class="value ${(data.recent_errors?.total_last_1h || 0) > 5 ? 'bad' : 'good'}">${fmtNum(data.recent_errors?.total_last_1h)}</div></div>
        ${data.last_error ? `
          <div class="stat"><label>Último error</label><div class="value sm">${data.last_error.error_type}</div></div>
          <div style="font-size:11px;color:#718096">${fmtTs(data.last_error?.failed_at)}</div>
        ` : '<div style="color:#68d391;font-size:13px;margin-top:8px">Sin errores recientes</div>'}
      </div>
      <div class="card">
        <h2>Scheduler</h2>
        ${renderScheduler(data.scheduler)}
      </div>
    </div>

    <!-- Rendimiento -->
    <div class="section-title">Rendimiento (últimas 24h)</div>
    <div class="grid grid-4">
      <div class="card">
        <h2>URLs</h2>
        <div class="stat"><label>Descubiertas</label><div class="value">${fmtNum(perf.urls_discovered)}</div></div>
        <div class="stat"><label>Nuevas</label><div class="value sm good">${fmtNum(perf.urls_new)}</div></div>
        <div class="stat"><label>Modificadas</label><div class="value sm warn">${fmtNum(perf.urls_changed)}</div></div>
      </div>
      <div class="card">
        <h2>Requests</h2>
        <div class="stat"><label>Total</label><div class="value">${fmtNum(perf.requests_total)}</div></div>
        <div class="stat"><label>Tasa de éxito</label><div class="value ${(perf.success_rate_pct || 100) >= 90 ? 'good' : 'bad'}">${fmtPct(perf.success_rate_pct)}</div></div>
        <div class="stat"><label>Fallidos</label><div class="value sm ${(perf.requests_failed || 0) > 0 ? 'bad' : 'good'}">${fmtNum(perf.requests_failed)}</div></div>
      </div>
      <div class="card">
        <h2>Latencia</h2>
        <div class="stat"><label>Promedio</label><div class="value">${fmtMs(perf.avg_latency_ms)}</div></div>
        <div class="stat"><label>Máxima</label><div class="value sm">${fmtMs(perf.max_latency_ms)}</div></div>
      </div>
      <div class="card">
        <h2>Protección</h2>
        <div class="stat"><label>Cooldowns hoy</label><div class="value ${protection.cooldowns_today > 0 ? 'warn' : 'good'}">${protection.cooldowns_today}</div></div>
        ${renderRateLimiters(protection.rate_limiter_states)}
      </div>
    </div>

    <!-- Errores -->
    <div class="section-title">Errores (últimas 24h)</div>
    <div class="grid grid-2">
      <div class="card">
        <h2>Por Tipo</h2>
        ${renderErrByType(errors.last_24h_by_type)}
      </div>
      <div class="card">
        <h2>Segmentos Problemáticos</h2>
        ${renderProblematic(errors.problematic_segments)}
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Últimos Errores</h2>
      ${renderErrors(errors.recent)}
    </div>

    <!-- Tendencias -->
    <div class="section-title">Tendencias (7 días)</div>
    <div class="grid grid-3">
      <div class="card">
        <h2>URLs Descubiertas / Día</h2>
        ${sparkline(trends.daily_urls_discovered, 'total')}
      </div>
      <div class="card">
        <h2>Errores / Día</h2>
        ${sparkline(trends.daily_errors, 'total')}
      </div>
      <div class="card">
        <h2>Latencia Promedio (ms)</h2>
        ${sparkline(trends.daily_avg_latency_ms, 'avg_ms')}
      </div>
    </div>
  `;

  // Status badge en header
  const badge = document.getElementById('status-badge');
  const totalErrors1h = data.recent_errors?.total_last_1h || 0;
  const inCooldown = Object.values(protection.rate_limiter_states || {}).some(r => r.in_cooldown);
  const hasFailed = data.segment_progress?.failed > 0;
  if (inCooldown || totalErrors1h > 10) {
    badge.textContent = 'ALERTA'; badge.className = 'err';
  } else if (hasFailed || totalErrors1h > 2) {
    badge.textContent = 'ADVERTENCIA'; badge.className = 'warn';
  } else {
    badge.textContent = 'OPERATIVO'; badge.className = 'ok';
  }
}

async function refresh() {
  try {
    const [summary, health] = await Promise.all([
      fetch('/ops/summary').then(r => r.json()),
      fetch('/health/discovery').then(r => r.json()),
    ]);
    const merged = { ...summary, ...health };
    render(merged);
    document.getElementById('last-updated').textContent =
      'Actualizado: ' + new Date().toLocaleTimeString('es-AR');
  } catch(e) {
    document.getElementById('status-badge').textContent = 'ERROR';
    document.getElementById('status-badge').className = 'err';
    console.error('Dashboard error:', e);
  }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def ops_dashboard():
    """Dashboard operativo HTML con auto-refresh cada 30s."""
    return HTMLResponse(content=_DASHBOARD_HTML)
