"""
discovery.engine.segment_discovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Algoritmo adaptativo de construcción de árbol de segmentos.
Portal-agnostic: opera a través de PortalAdapter.

Divide el espacio precio × superficie recursivamente hasta que cada
segmento hoja tenga total_count <= max_results_per_segment.

Dimensión elegida dinámicamente en cada nodo:
  1. Precio, mientras el rango de precio sea > min_price_range.
  2. Superficie, cuando el rango de precio ya es demasiado estrecho.
  3. Sin subdivisión posible → nodo oversized (hoja forzada).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from app.core.rate_limiter import CooldownError, get_rate_limiter
from discovery.engine.models import PortalAdapter, SegmentNode

log = logging.getLogger(__name__)


# ── Query de total_count vía adapter ─────────────────────────────────────────


async def _query_count(
    session,
    rate,
    adapter: PortalAdapter,
    node: SegmentNode,
) -> Optional[int]:
    try:
        await rate.wait()
    except CooldownError as exc:
        log.error("segment_discovery[%s]: cooldown — %s", adapter.portal, exc)
        return None

    payload = adapter.build_count_payload(
        page=1,
        operation_value=node.operation_value,
        location_value=node.location_value,
        price_min=node.price_min,
        price_max=node.price_max,
        surface_min=node.surface_min,
        surface_max=node.surface_max,
    )
    data = await session.post_json(adapter.api_url, payload)

    if data is None:
        rate.record_error()
        return None

    if "__http_error__" in data:
        status = data["__http_error__"]
        rate.record_error(http_status=status)
        if status in (403, 429):
            log.error("segment_discovery[%s]: HTTP %d — abortando nodo", adapter.portal, status)
        return None

    rate.record_success()
    return adapter.extract_total(data)


# ── Construcción recursiva del árbol ──────────────────────────────────────────


async def _build_tree(
    session,
    rate,
    adapter: PortalAdapter,
    cfg,
    node: SegmentNode,
    on_leaf_found: Optional[Callable[[SegmentNode], Awaitable[None]]],
) -> list[SegmentNode]:
    """Retorna la lista de hojas del subárbol con raíz en *node*."""
    count = await _query_count(session, rate, adapter, node)

    if count is None:
        log.warning(
            "segment_discovery[%s]: sin count op=%s loc=%s s=[%g-%g] p=[%g-%g] depth=%d → hoja con count=0",
            adapter.portal, node.operation_key, node.location_key,
            node.surface_min, node.surface_max, node.price_min, node.price_max, node.depth,
        )
        node.total_count = 0
        node.is_leaf = True
        if on_leaf_found:
            await on_leaf_found(node)
        return [node]

    node.total_count = count
    log.info(
        "segment_discovery[%s]: op=%-10s loc=%-25s s=[%6g-%6g] p=[%8g-%8g] depth=%d count=%d",
        adapter.portal, node.operation_key, node.location_key,
        node.surface_min, node.surface_max, node.price_min, node.price_max,
        node.depth, count,
    )

    if count <= cfg.max_results_per_segment:
        node.is_leaf = True
        if on_leaf_found:
            await on_leaf_found(node)
        return [node]

    if node.depth >= cfg.max_depth:
        node.is_leaf = True
        node.is_oversized = True
        log.warning(
            "segment_discovery[%s]: max_depth=%d → oversized_leaf op=%s loc=%s count=%d",
            adapter.portal, cfg.max_depth, node.operation_key, node.location_key, count,
        )
        if on_leaf_found:
            await on_leaf_found(node)
        return [node]

    children = _make_children(node, cfg)

    if not children:
        node.is_leaf = True
        node.is_oversized = True
        log.warning(
            "segment_discovery[%s]: sin subdivisión posible → oversized_leaf "
            "op=%s loc=%s s=[%g-%g] p=[%g-%g] count=%d "
            "(ancho_precio=%g ancho_sup=%g mín_precio=%g mín_sup=%g)",
            adapter.portal, node.operation_key, node.location_key,
            node.surface_min, node.surface_max, node.price_min, node.price_max, count,
            node.price_max - node.price_min, node.surface_max - node.surface_min,
            cfg.min_price_range, cfg.min_surface_range_m2,
        )
        if on_leaf_found:
            await on_leaf_found(node)
        return [node]

    leaves: list[SegmentNode] = []
    for child in children:
        child_leaves = await _build_tree(session, rate, adapter, cfg, child, on_leaf_found)
        leaves.extend(child_leaves)
    return leaves


def _make_children(node: SegmentNode, cfg) -> list[SegmentNode]:
    """
    Elige dinámicamente la dimensión de split por etapas:
      1. Precio, mientras ancho_precio > min_price_range.
      2. Superficie, cuando precio ya está demasiado fino.
      3. Lista vacía → el nodo se marcará como oversized.

    La elección es por nodo, no global: un segmento puede dividirse por precio
    varios niveles y luego pasar a superficie cuando el rango de precio se agota.
    """
    price_range = node.price_max - node.price_min
    surface_range = node.surface_max - node.surface_min

    if cfg.price_split_enabled and price_range > cfg.min_price_range:
        mid = (node.price_min + node.price_max) / 2
        log.debug(
            "_make_children: split precio p=[%g-%g] (rango=%g > mín=%g)",
            node.price_min, node.price_max, price_range, cfg.min_price_range,
        )
        return [
            _child(node, price_min=node.price_min, price_max=mid),
            _child(node, price_min=mid, price_max=node.price_max),
        ]

    if cfg.surface_split_enabled and surface_range > cfg.min_surface_range_m2:
        mid = (node.surface_min + node.surface_max) / 2
        log.debug(
            "_make_children: split superficie s=[%g-%g] (rango=%g > mín=%g) — precio agotado",
            node.surface_min, node.surface_max, surface_range, cfg.min_surface_range_m2,
        )
        return [
            _child(node, surface_min=node.surface_min, surface_max=mid),
            _child(node, surface_min=mid, surface_max=node.surface_max),
        ]

    return []


def _child(
    parent: SegmentNode,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    surface_min: Optional[float] = None,
    surface_max: Optional[float] = None,
) -> SegmentNode:
    return SegmentNode(
        portal=parent.portal,
        operation_key=parent.operation_key,
        operation_value=parent.operation_value,
        location_key=parent.location_key,
        location_value=parent.location_value,
        price_min=price_min if price_min is not None else parent.price_min,
        price_max=price_max if price_max is not None else parent.price_max,
        surface_min=surface_min if surface_min is not None else parent.surface_min,
        surface_max=surface_max if surface_max is not None else parent.surface_max,
        depth=parent.depth + 1,
        parent_db_id=parent.db_id,
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────


async def run_segment_discovery(
    adapter: PortalAdapter,
    cfg,
    on_leaf_found: Optional[Callable[[SegmentNode], Awaitable[None]]] = None,
) -> list[SegmentNode]:
    """
    Ejecuta el discovery de segmentos para todas las operaciones y
    ubicaciones definidas en *cfg*, usando el adapter del portal.

    *on_leaf_found(node)* se llama por cada segmento hoja confirmado,
    permitiendo persistencia incremental si el job falla a mitad.
    """
    rate = get_rate_limiter(adapter.rate_limiter_key)
    session = await adapter.create_session()

    all_leaves: list[SegmentNode] = []

    try:
        for op_key, op_val in cfg.operations.items():
            for loc_key, loc_val in cfg.locations.items():
                log.info(
                    "segment_discovery[%s]: árbol op=%s loc=%s",
                    adapter.portal, op_key, loc_key,
                )
                root = SegmentNode(
                    portal=adapter.portal,
                    operation_key=op_key,
                    operation_value=int(op_val),
                    location_key=loc_key,
                    location_value=int(loc_val),
                    price_min=cfg.min_price,
                    price_max=cfg.max_price,
                    surface_min=cfg.min_surface_m2,
                    surface_max=cfg.max_surface_m2,
                    depth=0,
                )
                leaves = await _build_tree(session, rate, adapter, cfg, root, on_leaf_found)
                all_leaves.extend(leaves)
                log.info(
                    "segment_discovery[%s]: op=%s loc=%s → %d hojas",
                    adapter.portal, op_key, loc_key, len(leaves),
                )
    finally:
        await session.close()

    return all_leaves
