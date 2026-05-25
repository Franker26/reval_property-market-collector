#!/usr/bin/env python3
"""
jobs/zonaprop_batch_extract.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Job batch con concurrencia controlada para extraer publicaciones de Zonaprop
a partir de un archivo JSONL de discovery.

Salidas:
    data/snapshots/zonaprop_snapshots_YYYY-MM-DD.jsonl
    data/errors/zonaprop_extract_errors_YYYY-MM-DD.jsonl

Uso típico:
    python jobs/zonaprop_batch_extract.py \\
        --input data/discovery/zonaprop_urls_2026-05-25.jsonl \\
        --limit 1000 --concurrency 5 --resume

Procesamiento por lotes reanudables:
    python jobs/zonaprop_batch_extract.py --offset 0    --limit 1000 --concurrency 5 --resume
    python jobs/zonaprop_batch_extract.py --offset 1000 --limit 1000 --concurrency 5 --resume
    python jobs/zonaprop_batch_extract.py --offset 2000 --limit 1000 --concurrency 5 --resume
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx

# ── sys.path ──────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Importar solo zonaprop + browser sin ejecutar sources/__init__.py ─────────
# sources/__init__.py importa FastAPI y todos los portales. El job solo necesita
# zonaprop.py y browser.py. Se inyecta un stub del paquete para evitar el init.
import types as _types

if "sources" not in sys.modules:
    _stub = _types.ModuleType("sources")
    _stub.__path__ = [str(_PROJECT_ROOT / "sources")]  # type: ignore[attr-defined]
    _stub.__package__ = "sources"
    sys.modules["sources"] = _stub

from sources.zonaprop import ZonapropSource  # noqa: E402
from sources import browser as _browser     # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_batch_extract")

# ── Constantes ────────────────────────────────────────────────────────────────

_AR_TIMEZONE = timezone(timedelta(hours=-3))

_DISCOVERY_DIR  = _PROJECT_ROOT / "data" / "discovery"
_SNAPSHOTS_DIR  = _PROJECT_ROOT / "data" / "snapshots"
_ERRORS_DIR     = _PROJECT_ROOT / "data" / "errors"
_RUNS_DIR       = _PROJECT_ROOT / "data" / "runs"

_DISCOVERY_FILENAME_TPL = "zonaprop_urls_{date}.jsonl"
_SNAPSHOTS_FILENAME_TPL = "zonaprop_snapshots_{date}.jsonl"
_ERRORS_FILENAME_TPL    = "zonaprop_extract_errors_{date}.jsonl"
_RUNS_FILENAME          = "zonaprop_runs.jsonl"

_DEFAULT_ALERT_THRESHOLD = 20.0   # % de errores que dispara la alerta

_HTTPX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


# ── Path helpers ──────────────────────────────────────────────────────────────

def _default_input_path(today: Optional[date] = None) -> Path:
    today = today or date.today()
    return _DISCOVERY_DIR / _DISCOVERY_FILENAME_TPL.format(date=today.isoformat())


def _snapshots_path(today: Optional[date] = None) -> Path:
    today = today or date.today()
    return _SNAPSHOTS_DIR / _SNAPSHOTS_FILENAME_TPL.format(date=today.isoformat())


def _errors_path(today: Optional[date] = None) -> Path:
    today = today or date.today()
    return _ERRORS_DIR / _ERRORS_FILENAME_TPL.format(date=today.isoformat())


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _read_discovery_jsonl(path: Path) -> list[dict]:
    """Lee todas las líneas JSON válidas del archivo de discovery."""
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                log.warning("Línea %d ignorada (JSON inválido): %s", lineno, exc)
    return records


def _load_external_ids(path: Path) -> set[str]:
    """Lee un JSONL y devuelve el set de external_id presentes."""
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
                eid = rec.get("external_id")
                if eid:
                    ids.add(str(eid))
            except json.JSONDecodeError:
                pass
    return ids


def _append_jsonl_sync(record: dict, path: Path) -> None:
    """Escribe una línea JSON. Debe llamarse con write_lock adquirido."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _write_run_record(stats: dict, args_dict: dict) -> Path:
    """
    Agrega una línea al historial de corridas en data/runs/zonaprop_runs.jsonl.
    Devuelve el path del archivo de runs.
    """
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs_path = _RUNS_DIR / _RUNS_FILENAME

    processed = stats["urls_to_process"]
    failed    = stats["failed"]
    error_rate = round(failed / processed * 100, 2) if processed else 0.0

    record = {
        "run_id"            : stats["run_id"],
        "date"              : stats["run_date"],
        "input"             : stats["input"],
        "urls_read"         : stats["urls_read"],
        "unique_urls"       : stats["unique_urls"],
        "input_duplicates"  : stats["input_duplicates"],
        "selected"          : stats["selected"],
        "skipped_resume"    : stats["skipped_resume"],
        "skipped_errors"    : stats["skipped_errors"],
        "urls_processed"    : processed,
        "successful"        : stats["successful"],
        "failed"            : failed,
        "error_rate_pct"    : error_rate,
        "top_errors"        : stats["top_errors"],
        "elapsed_seconds"   : round(stats["elapsed_seconds"], 1),
        "avg_rate_per_min"  : round(stats["avg_rate_per_min"], 1),
        "concurrency"       : stats["concurrency"],
        "offset"            : args_dict.get("offset", 0),
        "limit"             : args_dict.get("limit"),
        "resume"            : args_dict.get("resume", False),
        "skip_errors"       : args_dict.get("skip_errors", False),
        "timeout"           : args_dict.get("timeout"),
        "delay"             : args_dict.get("delay"),
        "snapshots_path"    : stats["snapshots_path"],
        "errors_path"       : stats["errors_path"],
    }
    _append_jsonl_sync(record, runs_path)
    return runs_path


# ── Estado compartido del batch ───────────────────────────────────────────────

class _BatchState:
    """
    Contadores y métricas compartidas entre las corutinas del batch.
    En asyncio (single-thread), los enteros son seguros sin lock.
    El write_lock protege las escrituras a disco.
    """

    def __init__(self, total: int, progress_every: int) -> None:
        self.total          = total
        self.progress_every = progress_every
        self.completed      = 0
        self.successful     = 0
        self.failed         = 0
        self.error_types:   dict[str, int] = {}   # {"TimeoutError": 12, ...}
        self.start_time     = time.monotonic()
        self.write_lock     = asyncio.Lock()

    def record_success(self) -> None:
        self.completed  += 1
        self.successful += 1

    def record_error(self, error_type: str = "Unknown") -> None:
        self.completed += 1
        self.failed    += 1
        self.error_types[error_type] = self.error_types.get(error_type, 0) + 1

    def top_errors(self, n: int = 5) -> dict[str, int]:
        """Devuelve los N tipos de error más frecuentes, ordenados."""
        return dict(sorted(self.error_types.items(), key=lambda x: x[1], reverse=True)[:n])

    def should_log_progress(self) -> bool:
        return (
            self.progress_every > 0
            and (
                self.completed % self.progress_every == 0
                or self.completed == self.total
            )
        )

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    def avg_rate_per_min(self) -> float:
        elapsed = self.elapsed_seconds()
        if elapsed < 0.1 or self.completed == 0:
            return 0.0
        return self.completed / elapsed * 60.0

    def eta_str(self) -> str:
        rate_per_sec = self.completed / max(self.elapsed_seconds(), 0.1)
        remaining = self.total - self.completed
        if rate_per_sec <= 0 or remaining <= 0:
            return "—"
        eta_secs = int(remaining / rate_per_sec)
        hours, rem = divmod(eta_secs, 3600)
        mins, secs = divmod(rem, 60)
        if hours:
            return f"~{hours}h {mins:02d}m"
        elif mins:
            return f"~{mins}m {secs:02d}s"
        else:
            return f"~{secs}s"


# ── Worker ────────────────────────────────────────────────────────────────────

async def _process_one(
    idx: int,
    rec: dict,
    source: ZonapropSource,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    state: _BatchState,
    snapshots_path: Path,
    errors_path: Path,
    timeout: float,
    delay: float,
) -> None:
    """Procesa una URL: extrae, escribe resultado o error, actualiza estado."""
    url         = rec.get("url", "").strip()
    external_id = rec.get("external_id")

    async with sem:
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            result = await asyncio.wait_for(
                source.extract(url, client),
                timeout=timeout,
            )
            async with state.write_lock:
                _append_jsonl_sync(result, snapshots_path)
            state.record_success()
            log.debug("✓ [%d/%d] id=%s", idx, state.total, external_id)

        except asyncio.TimeoutError:
            error_record = {
                "source": "zonaprop",
                "url": url,
                "external_id": external_id,
                "error_type": "TimeoutError",
                "error_message": f"Timeout after {timeout}s",
                "failed_at": datetime.now(_AR_TIMEZONE).isoformat(),
            }
            async with state.write_lock:
                _append_jsonl_sync(error_record, errors_path)
            state.record_error("TimeoutError")
            log.warning("✗ TIMEOUT [%d/%d] id=%s url=%s", idx, state.total, external_id, url)

        except Exception as exc:
            etype = type(exc).__name__
            error_record = {
                "source": "zonaprop",
                "url": url,
                "external_id": external_id,
                "error_type": etype,
                "error_message": str(exc),
                "failed_at": datetime.now(_AR_TIMEZONE).isoformat(),
            }
            async with state.write_lock:
                _append_jsonl_sync(error_record, errors_path)
            state.record_error(etype)
            log.warning(
                "✗ ERROR [%d/%d] id=%s → %s: %s",
                idx, state.total, external_id, etype, exc,
            )

    # Progreso (fuera del semaphore para no bloquear otros workers)
    if state.should_log_progress():
        log.info(
            "PROGRESO [%d/%d]  ✓ %d  ✗ %d  rate=%.1f/min  ETA=%s",
            state.completed, state.total,
            state.successful, state.failed,
            state.avg_rate_per_min(),
            state.eta_str(),
        )


# ── Núcleo async ──────────────────────────────────────────────────────────────

async def run_batch(
    input_path: Path,
    snapshots_path: Path,
    errors_path: Path,
    limit: Optional[int],
    offset: int,
    concurrency: int,
    resume: bool,
    skip_errors: bool,
    progress_every: int,
    delay: float,
    timeout: float,
    alert_threshold: float,
) -> dict:
    """
    Orquesta la extracción batch con concurrencia controlada.
    Devuelve un dict con métricas completas del batch.
    """
    # ── Leer discovery ────────────────────────────────────────────────────────
    log.info("Leyendo discovery: %s", input_path)
    all_records = _read_discovery_jsonl(input_path)
    total_read = len(all_records)
    log.info("  %d líneas leídas", total_read)

    # ── Deduplicar por URL ────────────────────────────────────────────────────
    seen_urls: set[str] = set()
    unique_records: list[dict] = []
    input_duplicates = 0
    for rec in all_records:
        url = rec.get("url", "").strip()
        if not url:
            continue
        if url in seen_urls:
            input_duplicates += 1
            continue
        seen_urls.add(url)
        unique_records.append(rec)

    if input_duplicates:
        log.info("  %d duplicados en input omitidos", input_duplicates)

    # ── Aplicar offset / limit ────────────────────────────────────────────────
    slice_end = (offset + limit) if limit is not None else None
    selected = unique_records[offset:slice_end]
    log.info(
        "  offset=%d  limit=%s  → %d URLs seleccionadas",
        offset,
        str(limit) if limit is not None else "sin límite",
        len(selected),
    )

    # ── Resume: cargar external_ids ya procesados ─────────────────────────────
    resumed_ids: set[str] = set()
    if resume:
        resumed_ids = _load_external_ids(snapshots_path)
        if resumed_ids:
            log.info("  --resume: %d snapshots existentes cargados", len(resumed_ids))

    # ── Skip-errors: cargar external_ids que ya fallaron ─────────────────────
    skipped_error_ids: set[str] = set()
    if skip_errors:
        skipped_error_ids = _load_external_ids(errors_path)
        if skipped_error_ids:
            log.info("  --skip-errors: %d errores previos cargados", len(skipped_error_ids))

    # ── Filtrar batch final ───────────────────────────────────────────────────
    batch: list[dict] = []
    skipped_resume  = 0
    skipped_errors  = 0
    for rec in selected:
        eid = str(rec.get("external_id", ""))
        if eid and eid in resumed_ids:
            skipped_resume += 1
            continue
        if eid and eid in skipped_error_ids:
            skipped_errors += 1
            continue
        batch.append(rec)

    total_to_process = len(batch)

    if skipped_resume:
        log.info("  %d omitidas por --resume (ya en snapshots)", skipped_resume)
    if skipped_errors:
        log.info("  %d omitidas por --skip-errors (errores previos)", skipped_errors)
    log.info("  → %d URLs a procesar efectivamente", total_to_process)

    run_id   = datetime.now(_AR_TIMEZONE).isoformat()
    run_date = date.today().isoformat()

    if total_to_process == 0:
        log.warning("No hay URLs nuevas a procesar.")
        return {
            "run_id"             : run_id,
            "run_date"           : run_date,
            "input"              : input_path.name,
            "urls_read"          : total_read,
            "unique_urls"        : len(unique_records),
            "input_duplicates"   : input_duplicates,
            "selected"           : len(selected),
            "skipped_resume"     : skipped_resume,
            "skipped_errors"     : skipped_errors,
            "urls_to_process"    : 0,
            "successful"         : 0,
            "failed"             : 0,
            "top_errors"         : {},
            "elapsed_seconds"    : 0.0,
            "avg_rate_per_min"   : 0.0,
            "concurrency"        : concurrency,
            "snapshots_path"     : str(snapshots_path),
            "errors_path"        : str(errors_path),
        }

    # ── Crear dirs de salida ──────────────────────────────────────────────────
    snapshots_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Inicializar browser Playwright ────────────────────────────────────────
    log.info("Inicializando browser Playwright…")
    await _browser.get_browser()
    log.info("  Browser listo. Iniciando batch con concurrency=%d", concurrency)

    state  = _BatchState(total=total_to_process, progress_every=progress_every)
    sem    = asyncio.Semaphore(concurrency)
    source = ZonapropSource()

    try:
        async with httpx.AsyncClient(
            headers=_HTTPX_HEADERS,
            follow_redirects=True,
            timeout=timeout + 5,  # httpx timeout > asyncio.wait_for timeout
        ) as client:
            tasks = [
                asyncio.create_task(
                    _process_one(
                        idx=idx,
                        rec=rec,
                        source=source,
                        client=client,
                        sem=sem,
                        state=state,
                        snapshots_path=snapshots_path,
                        errors_path=errors_path,
                        timeout=timeout,
                        delay=delay,
                    )
                )
                for idx, rec in enumerate(batch, 1)
            ]
            await asyncio.gather(*tasks)

    finally:
        log.info("Cerrando browser Playwright…")
        await _browser.close()

    return {
        "run_id"            : run_id,
        "run_date"          : run_date,
        "input"             : input_path.name,
        "urls_read"         : total_read,
        "unique_urls"       : len(unique_records),
        "input_duplicates"  : input_duplicates,
        "selected"          : len(selected),
        "skipped_resume"    : skipped_resume,
        "skipped_errors"    : skipped_errors,
        "urls_to_process"   : total_to_process,
        "successful"        : state.successful,
        "failed"            : state.failed,
        "top_errors"        : state.top_errors(),
        "elapsed_seconds"   : state.elapsed_seconds(),
        "avg_rate_per_min"  : state.avg_rate_per_min(),
        "concurrency"       : concurrency,
        "snapshots_path"    : str(snapshots_path),
        "errors_path"       : str(errors_path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch extraction de Zonaprop con concurrencia controlada.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Primeras 1000 URLs con concurrencia 5 y reanudación
  python jobs/zonaprop_batch_extract.py --limit 1000 --concurrency 5 --resume

  # Lotes reanudables de 1000
  python jobs/zonaprop_batch_extract.py --offset 0    --limit 1000 --concurrency 5 --resume
  python jobs/zonaprop_batch_extract.py --offset 1000 --limit 1000 --concurrency 5 --resume

  # Ignorar errores anteriores + delay entre requests
  python jobs/zonaprop_batch_extract.py --limit 500 --concurrency 3 --skip-errors --delay 1
        """,
    )

    parser.add_argument(
        "--input", type=Path, default=None,
        help="JSONL de discovery. Default: data/discovery/zonaprop_urls_YYYY-MM-DD.jsonl",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Máximo de URLs a procesar.",
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="URLs a saltear antes de empezar (default: 0).",
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="Máximo de extracciones simultáneas (default: 5).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Saltar URLs cuyo external_id ya está en el archivo de snapshots.",
    )
    parser.add_argument(
        "--skip-errors", action="store_true",
        help="Saltar URLs cuyo external_id ya está en el archivo de errores.",
    )
    parser.add_argument(
        "--progress-every", type=int, default=100,
        metavar="N",
        help="Loguear progreso cada N URLs completadas (default: 100).",
    )
    parser.add_argument(
        "--delay", type=float, default=0.0,
        metavar="SECONDS",
        help="Pausa en segundos entre requests por worker (default: 0).",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        metavar="SECONDS",
        help="Timeout por extracción en segundos (default: 30).",
    )
    parser.add_argument(
        "--alert-threshold", type=float, default=_DEFAULT_ALERT_THRESHOLD,
        metavar="PCT",
        help=(
            "Porcentaje de errores que dispara una alerta al finalizar "
            f"(default: {_DEFAULT_ALERT_THRESHOLD})."
        ),
    )

    return parser.parse_args()


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> int:
    args = _parse_args()
    today = date.today()

    input_path     = args.input or _default_input_path(today)
    snap_path      = _snapshots_path(today)
    err_path       = _errors_path(today)

    if not input_path.exists():
        log.error("Archivo de discovery no encontrado: %s", input_path)
        return 1

    log.info("=" * 60)
    log.info("Zonaprop batch extract — iniciando")
    log.info("  input          : %s", input_path)
    log.info("  snapshots      : %s", snap_path)
    log.info("  errors         : %s", err_path)
    log.info("  offset         : %d", args.offset)
    log.info("  limit          : %s", str(args.limit) if args.limit else "sin límite")
    log.info("  concurrency    : %d", args.concurrency)
    log.info("  resume         : %s", args.resume)
    log.info("  skip-errors    : %s", args.skip_errors)
    log.info("  progress-every : %d", args.progress_every)
    log.info("  delay          : %.1fs", args.delay)
    log.info("  timeout        : %.1fs", args.timeout)
    log.info("  alert-threshold: %.1f%%", args.alert_threshold)
    log.info("=" * 60)

    stats = asyncio.run(
        run_batch(
            input_path      = input_path,
            snapshots_path  = snap_path,
            errors_path     = err_path,
            limit           = args.limit,
            offset          = args.offset,
            concurrency     = args.concurrency,
            resume          = args.resume,
            skip_errors     = args.skip_errors,
            progress_every  = args.progress_every,
            delay           = args.delay,
            timeout         = args.timeout,
            alert_threshold = args.alert_threshold,
        )
    )

    # ── Resumen ───────────────────────────────────────────────────────────────
    elapsed   = stats["elapsed_seconds"]
    mins, sec = divmod(int(elapsed), 60)
    hrs,  mins = divmod(mins, 60)
    elapsed_str = (
        f"{hrs}h {mins:02d}m {sec:02d}s" if hrs
        else f"{mins}m {sec:02d}s"        if mins
        else f"{sec}s"
    )

    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("  input                    : %s", input_path)
    log.info("  snapshot output          : %s", stats["snapshots_path"])
    log.info("  errors output            : %s", stats["errors_path"])
    log.info("  ─────────────────────────────────────────────")
    log.info("  URLs leídas              : %d", stats["urls_read"])
    log.info("  URLs únicas              : %d", stats["unique_urls"])
    log.info("  Duplicados input         : %d", stats["input_duplicates"])
    log.info("  URLs seleccionadas       : %d", stats["selected"])
    log.info("  Omitidas por --resume    : %d", stats["skipped_resume"])
    log.info("  Omitidas por --skip-err  : %d", stats["skipped_errors"])
    log.info("  URLs procesadas          : %d", stats["urls_to_process"])
    log.info("  ─────────────────────────────────────────────")
    log.info("  Exitosas                 : %d", stats["successful"])
    log.info("  Fallidas                 : %d", stats["failed"])
    log.info("  ─────────────────────────────────────────────")
    log.info("  Duración total           : %s", elapsed_str)
    log.info("  Velocidad promedio       : %.1f URLs/min", stats["avg_rate_per_min"])
    log.info("  Concurrencia usada       : %d", stats["concurrency"])
    if stats["top_errors"]:
        log.info("  Top errores              : %s", stats["top_errors"])
    log.info("=" * 60)

    # ── Guardar registro de la corrida ────────────────────────────────────────
    args_dict = vars(args)
    runs_path = _write_run_record(stats, args_dict)
    log.info("Run registrado → %s", runs_path)

    # ── Alerta por tasa de error ──────────────────────────────────────────────
    processed   = stats["urls_to_process"]
    error_rate  = stats["failed"] / processed * 100 if processed else 0.0
    if processed > 0 and error_rate >= args.alert_threshold:
        log.warning("")
        log.warning("!" * 60)
        log.warning("⚠  ALERTA: tasa de error %.1f%% ≥ umbral %.1f%%",
                    error_rate, args.alert_threshold)
        log.warning("   Fallidas  : %d / %d URLs", stats["failed"], processed)
        log.warning("   Top errores: %s", stats["top_errors"])
        log.warning("   Revisar   : %s", stats["errors_path"])
        log.warning("!" * 60)
        log.warning("")

    return 0 if stats["successful"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
