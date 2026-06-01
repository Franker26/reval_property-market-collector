#!/usr/bin/env python3
"""
Test standalone de la lógica Python en invalidate_changed_segments_after_discovery.
No requiere DB ni imports del proyecto — testea el cálculo de delta, umbrales,
prioridades y reason string directamente.
"""


def compute(rows, delta_abs_normal=30, delta_abs_high=100, delta_pct_normal=2.0, delta_pct_high=10.0):
    """Espejo exacto del bloque Python en invalidate_changed_segments_after_discovery."""
    to_invalidate = []
    for row in rows:
        current_count = row["current_count"] or 0
        prev_count = row["prev_count"] or 0
        delta_abs = current_count - prev_count

        if prev_count == 0:
            delta_pct = 100.0 if current_count > 0 else 0.0
        else:
            delta_pct = abs(delta_abs) / prev_count * 100

        if abs(delta_abs) >= delta_abs_normal or delta_pct >= delta_pct_normal:
            priority = (
                "high"
                if abs(delta_abs) >= delta_abs_high or delta_pct >= delta_pct_high
                else "normal"
            )
            reason = (
                f"count_delta: prev={prev_count} current={current_count}"
                f" delta={delta_abs:+d} pct={delta_pct:.1f}%"
            )
            to_invalidate.append({
                "queue_id": row["queue_id"],
                "segment_id": row["segment_id"],
                "delta_abs": delta_abs,
                "delta_pct": delta_pct,
                "priority": priority,
                "reason": reason,
            })
    return to_invalidate


def row(qid, seg_id, prev, current):
    return {"queue_id": qid, "segment_id": seg_id, "prev_count": prev, "current_count": current}


cases = [
    # (descripción, prev, current, should_invalidate, expected_priority)
    ("Sin cambio",                        1000, 1000, False, None),
    ("Delta bajo umbral abs (14)",        1000, 1014, False, None),
    ("Delta bajo umbral pct (1.4%)",      1000, 1014, False, None),
    ("Delta abs normal (35, 3.5%)",       1000, 1035, True,  "normal"),
    ("Delta abs high (110, 11%)",         1000, 1110, True,  "high"),
    ("Pct normal exacto (2.5%)",          1000, 1025, True,  "normal"),
    ("Pct high exacto (15%)",             1000, 1150, True,  "high"),
    ("Bajada fuerte (−200, 11.1%)",       1800, 1600, True,  "high"),
    ("Bajada normal (−35, 3.5%)",         1000,  965, True,  "normal"),
    ("Bajada pequeña (−14, 1.4%)",        1000,  986, False, None),
    ("prev=0, current>0 (100%)",             0,   50, True,  "high"),
    ("prev=0, current=0",                    0,    0, False, None),
    ("Delta abs 100 exacto (high)",       1000, 1100, True,  "high"),
    ("Delta abs 99 (normal)",             1000, 1099, True,  "normal"),
    ("Delta pct 10% exacto (high)",       1000, 1100, True,  "high"),
    ("Delta pct 9.9% (normal si abs<100)",1010, 1110, True,  "high"),  # abs=100 → high
    ("Delta pct 9.9% abs<100",            2000, 2198, True,  "normal"),  # abs=198 → high... actually 9.9% of 2000=198 → high by abs
]

# Recalculate last case: 9.9% of 2000 = 198, abs=198 >= 100 → high. Let me fix:
cases[-1] = ("Delta pct 9% abs<100", 1100, 1199, True, "normal")  # abs=99, pct=9% → normal


print("=" * 72)
print("TEST: invalidate_changed_segments_after_discovery — lógica Python")
print("=" * 72)

passed = failed = 0

for i, (name, prev, current, should_invalidate, expected_priority) in enumerate(cases):
    r = row(i + 1, 1000 + i, prev, current)
    result = compute([r])
    was_invalidated = len(result) > 0

    ok = was_invalidated == should_invalidate
    if ok and was_invalidated:
        ok = result[0]["priority"] == expected_priority

    if ok:
        passed += 1
        marker = "PASS"
    else:
        failed += 1
        marker = "FAIL"

    detail = ""
    if was_invalidated:
        x = result[0]
        detail = f"  →  priority={x['priority']}  delta={x['delta_abs']:+d}  pct={x['delta_pct']:.1f}%"
        if not ok:
            detail += f"  (esperado: priority={expected_priority})"
    elif not should_invalidate:
        detail = "  →  no invalidado (correcto)"
    else:
        detail = f"  →  no invalidado  (esperado: sí, priority={expected_priority})"

    print(f"  {marker}  {name}{detail}")

print("=" * 72)
print(f"Resultado: {passed} pasaron, {failed} fallaron")

# Verificar formato de reason string
print()
print("Verificando reason string:")
sample = compute([row(99, 999, 1250, 1410)])
assert sample, "debería haber invalidado"
expected_reason = "count_delta: prev=1250 current=1410 delta=+160 pct=12.8%"
actual_reason = sample[0]["reason"]
if actual_reason == expected_reason:
    print(f"  PASS  reason: {actual_reason!r}")
    passed += 1
else:
    print(f"  FAIL  reason esperado: {expected_reason!r}")
    print(f"        reason obtenido: {actual_reason!r}")
    failed += 1

# Verificar top10 ordenado por |delta_abs|
print()
print("Verificando top10 (orden por |delta_abs| desc):")
many = [row(i, i, 1000, 1000 + d) for i, d in enumerate([50, 200, 30, 150, 80, 40, 300, 60, 100, 120, 90])]
result = compute(many)
top10 = sorted(result, key=lambda x: abs(x["delta_abs"]), reverse=True)[:10]
deltas = [abs(x["delta_abs"]) for x in top10]
is_sorted = deltas == sorted(deltas, reverse=True)
if is_sorted and len(top10) == 10:
    print(f"  PASS  top10 ordenado: {deltas}")
    passed += 1
else:
    print(f"  FAIL  top10: {deltas}")
    failed += 1

print()
print(f"TOTAL: {passed} pasaron, {failed} fallaron")

import sys
sys.exit(0 if failed == 0 else 1)
