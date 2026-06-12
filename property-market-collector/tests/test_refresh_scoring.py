#!/usr/bin/env python3
"""
Test standalone del refresh rotativo Etapa B (churn observado).

Importa las funciones puras reales (compute_churn_daily, blend_churn_ewma,
_build_refresh_reason) y espeja la lógica de scoring/tier/presupuesto de
select_segments_due_for_refresh (que es async y DB-bound), igual que
test_invalidate_logic.py espeja la invalidación estructural.

No requiere DB. Correr: python tests/test_refresh_scoring.py
"""
import math
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.repositories.zonaprop.segments import blend_churn_ewma, compute_churn_daily  # noqa: E402
from app.repositories.zonaprop.scan_queue import _build_refresh_reason  # noqa: E402


# ── Config de referencia (defaults de RefreshConfig) ──────────────────────────

CFG = {
    "gap_hours_hot": 24.0,
    "gap_hours_warm": 72.0,
    "gap_hours_cold": 168.0,
    "gap_hours_unknown": 72.0,
    "weight_churn": 0.50,
    "weight_volatility": 0.25,
    "weight_volume": 0.25,
    "churn_cap": 0.05,
    "min_churn_samples": 1,
    "hot_score_threshold": 0.70,
    "warm_score_threshold": 0.35,
    "high_volume_threshold": 1500,
    "volatility_cap": 0.10,
    "volume_norm_divisor": 2000.0,
    "max_segments_per_cycle": 50,
    "max_pages_per_cycle": 1500,
    "postings_per_page": 20,
}


def score_and_tier(churn_ewma, churn_samples, count_volatility, total_count, cfg=CFG):
    """Espejo del scoring v2 de select_segments_due_for_refresh."""
    has_churn_evidence = churn_samples >= cfg["min_churn_samples"]
    churn_norm = 0.0
    if has_churn_evidence and churn_ewma is not None and cfg["churn_cap"] > 0:
        churn_norm = min(churn_ewma / cfg["churn_cap"], 1.0)
    v_norm = min(count_volatility / cfg["volatility_cap"], 1.0) if cfg["volatility_cap"] > 0 else 0.0
    s_norm = min(total_count / cfg["volume_norm_divisor"], 1.0) if cfg["volume_norm_divisor"] > 0 else 0.0
    score = (
        cfg["weight_churn"] * churn_norm
        + cfg["weight_volatility"] * v_norm
        + cfg["weight_volume"] * s_norm
    )
    if not has_churn_evidence:
        tier = "unknown"
    elif score >= cfg["hot_score_threshold"]:
        tier = "hot"
    elif score >= cfg["warm_score_threshold"] or total_count >= cfg["high_volume_threshold"]:
        tier = "warm"
    else:
        tier = "cold"
    return score, tier


def select_with_budget(candidates, cfg=CFG):
    """Espejo del corte por presupuesto de páginas + tope de segmentos."""
    selected, pages_used, skipped = [], 0, []
    for cand in candidates:
        if len(selected) >= cfg["max_segments_per_cycle"]:
            break
        if pages_used + cand["estimated_pages"] > cfg["max_pages_per_cycle"]:
            skipped.append(cand)
            continue
        pages_used += cand["estimated_pages"]
        selected.append(cand)
    return selected, pages_used, skipped


def inherit(parent_ewma, parent_samples, samples_cap):
    """Espejo de la herencia de prior débil en splits (inherit_churn_from_parents)."""
    return {"churn_ewma": parent_ewma, "churn_samples_count": min(parent_samples, samples_cap)}


# ── Runner ────────────────────────────────────────────────────────────────────

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{'  → ' + detail if detail else ''}")


print("=" * 72)
print("churn_raw + normalización temporal")
print("=" * 72)

# Mismo churn crudo tras 1 día vs 14 días → churn diario 14x distinto
c1 = compute_churn_daily(new_count=10, changed_count=10, listings_found=200, elapsed_days=1.0)
c14 = compute_churn_daily(new_count=10, changed_count=10, listings_found=200, elapsed_days=14.0)
check("churn_raw = 0.10 tras 1 día → churn_daily 0.10", abs(c1 - 0.10) < 1e-9, f"got {c1}")
check("mismo churn_raw tras 14 días → 14x menor", abs(c14 - 0.10 / 14) < 1e-9, f"got {c14}")
check("relación exacta 14x", abs(c1 / c14 - 14.0) < 1e-6)

# Guard: listings_found == 0 → None (no es evidencia de churn 0)
check("guard listings_found=0 → None", compute_churn_daily(5, 5, 0, 1.0) is None)

# Piso temporal: rescan inmediato no explota la división
fast = compute_churn_daily(10, 10, 200, elapsed_days=0.01)
check("piso 0.5 días contra rescans inmediatos", abs(fast - 0.10 / 0.5) < 1e-9, f"got {fast}")

# Clamp de extremos a 1.0
extreme = compute_churn_daily(200, 0, 200, elapsed_days=0.5)
check("clamp churn_daily ≤ 1.0", extreme == 1.0, f"got {extreme}")

print()
print("=" * 72)
print("EWMA")
print("=" * 72)

check("primer valor (sin previo) = churn", blend_churn_ewma(0.04, None, 0.5) == 0.04)
check("suavizado alpha=0.5", abs(blend_churn_ewma(0.08, 0.02, 0.5) - 0.05) < 1e-9)
check("mezcla con prior heredado", abs(blend_churn_ewma(0.10, 0.02, 0.5) - 0.06) < 1e-9)

print()
print("=" * 72)
print("score v2 + tiers")
print("=" * 72)

# El caso que Etapa A fallaba: total_count estable (volatilidad 0) pero churn alto
score, tier = score_and_tier(churn_ewma=0.05, churn_samples=3, count_volatility=0.0, total_count=200)
check("churn alto + count estable → NO cold", tier in ("hot", "warm"), f"tier={tier} score={score:.3f}")

# churn al cap + algo de volumen → hot
score, tier = score_and_tier(churn_ewma=0.06, churn_samples=3, count_volatility=0.02, total_count=1200)
check("churn al cap + señales secundarias → hot", tier == "hot", f"tier={tier} score={score:.3f}")

# Sin muestras suficientes → unknown aunque haya churn_ewma heredado
score, tier = score_and_tier(churn_ewma=0.05, churn_samples=0, count_volatility=0.0, total_count=200)
check("prior heredado SIN muestras propias → unknown (no habilita score v2)", tier == "unknown")

# churn NULL y sin muestras → unknown
_, tier = score_and_tier(churn_ewma=None, churn_samples=0, count_volatility=0.08, total_count=1800)
check("churn NULL → unknown (no cold, no warm por volumen)", tier == "unknown")

# Con evidencia y churn bajo → cold (dentro del gap máximo de negocio)
_, tier = score_and_tier(churn_ewma=0.001, churn_samples=2, count_volatility=0.0, total_count=100)
check("churn bajo con evidencia → cold", tier == "cold")

# Volumen alto fuerza warm como piso (no cold)
_, tier = score_and_tier(churn_ewma=0.001, churn_samples=2, count_volatility=0.0, total_count=1600)
check("alto volumen → warm como piso", tier == "warm")

# min_churn_samples=2: una sola muestra sigue unknown
cfg2 = dict(CFG, min_churn_samples=2)
_, tier = score_and_tier(churn_ewma=0.05, churn_samples=1, count_volatility=0.0, total_count=200, cfg=cfg2)
check("min_samples=2 con 1 muestra → unknown", tier == "unknown")

print()
print("=" * 72)
print("herencia en split (cap = min_samples − 1)")
print("=" * 72)

child = inherit(parent_ewma=0.04, parent_samples=5, samples_cap=0)
check("hijo hereda churn_ewma del padre", child["churn_ewma"] == 0.04)
check("cap=0 → 0 muestras heredadas", child["churn_samples_count"] == 0)
_, tier = score_and_tier(child["churn_ewma"], child["churn_samples_count"], 0.0, 200)
check("hijo queda unknown hasta churn propio", tier == "unknown")
# Primera observación propia: el prior heredado inicializa el EWMA
first_own = blend_churn_ewma(0.10, child["churn_ewma"], 0.5)
check("prior influye en primera observación propia", abs(first_own - 0.07) < 1e-9, f"got {first_own}")
_, tier = score_and_tier(first_own, 1, 0.0, 200)
check("con 1 muestra propia sale de unknown", tier != "unknown", f"tier={tier}")

print()
print("=" * 72)
print("presupuesto por páginas")
print("=" * 72)


def cand(seg_id, pages, overdue=2.0):
    return {"segment_id": seg_id, "estimated_pages": pages, "overdue_ratio": overdue}


# Oversized que excede el presupuesto se salta sin romper la selección
candidates = [cand(1, 1400), cand(2, 2000), cand(3, 90)]
selected, pages_used, skipped = select_with_budget(candidates)
check(
    "oversized se salta, los demás entran",
    [c["segment_id"] for c in selected] == [1, 3] and [c["segment_id"] for c in skipped] == [2],
    f"selected={[c['segment_id'] for c in selected]} skipped={[c['segment_id'] for c in skipped]}",
)
check("páginas usadas dentro del presupuesto", pages_used <= CFG["max_pages_per_cycle"], f"used={pages_used}")

# Tope secundario por cantidad de segmentos
many = [cand(i, 1) for i in range(100)]
selected, _, _ = select_with_budget(many)
check("tope secundario max_segments_per_cycle", len(selected) == CFG["max_segments_per_cycle"])

# estimated_pages mínimo 1 página
check("estimated_pages mínimo 1", max(math.ceil(5 / CFG["postings_per_page"]), 1) == 1)

print()
print("=" * 72)
print("invariantes de negocio + reason")
print("=" * 72)

max_gap = max(CFG["gap_hours_hot"], CFG["gap_hours_warm"], CFG["gap_hours_cold"], CFG["gap_hours_unknown"])
check("ningún tier supera el gap máximo de negocio (cold=168h)", max_gap == CFG["gap_hours_cold"])
check("unknown más agresivo que cold", CFG["gap_hours_unknown"] < CFG["gap_hours_cold"])
check("pesos suman 1.0", abs(CFG["weight_churn"] + CFG["weight_volatility"] + CFG["weight_volume"] - 1.0) < 1e-9)

reason = _build_refresh_reason({
    "tier": "hot", "age_hours": 30.5, "gap_hours": 24.0, "score": 0.81,
    "churn_ewma": 0.042, "churn_samples": 3, "count_volatility": 0.01,
    "total_count": 850, "estimated_pages": 43,
})
expected = (
    "refresh:hot;age_hours=30.5;gap_hours=24.0;score=0.81;churn_ewma=0.042;"
    "churn_samples=3;count_volatility=0.01;volume=850;estimated_pages=43"
)
check("reason refresh reconstruye la decisión", reason == expected, f"got {reason!r}")

reason_unknown = _build_refresh_reason({
    "tier": "unknown", "age_hours": 80.0, "gap_hours": 72.0,
    "churn_samples": 0, "estimated_pages": 10,
})
expected_unknown = (
    "refresh:unknown;age_hours=80.0;gap_hours=72.0;churn_samples=0;"
    "reason=no_churn_history;estimated_pages=10"
)
check("reason unknown explicita no_churn_history", reason_unknown == expected_unknown, f"got {reason_unknown!r}")

print()
print(f"TOTAL: {passed} pasaron, {failed} fallaron")
sys.exit(0 if failed == 0 else 1)
