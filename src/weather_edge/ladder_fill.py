from __future__ import annotations

from typing import Any

from .audit import write_json_gz
from .clients import clob
from .config import Settings
from .ladder import LadderCandidate, LadderLeg


def _leg_rejection_reasons(leg: LadderLeg, requested_usd: float, fill: Any | None) -> list[str]:
    reasons: list[str] = []
    if not leg.token_id:
        reasons.append(f"leg {leg.market_id} missing token_id")
        return reasons
    if fill is None:
        reasons.append(f"leg {leg.market_id} fill simulation failed")
        return reasons
    if fill.avg_price is None:
        reasons.append(f"leg {leg.market_id} missing avg_price")
    if not bool(fill.filled):
        reasons.append(f"leg {leg.market_id} not fully fillable")
    if float(fill.cost_usd or 0.0) < requested_usd * 0.999:
        reasons.append(f"leg {leg.market_id} filled cost below requested_usd")
    return reasons


def simulate_ladder_fill(
    settings: Settings,
    ladder: LadderCandidate,
    *,
    requested_usd_per_leg: float = 1.0,
    max_avg_price: float = 0.10,
) -> dict[str, Any]:
    """Simulate per-leg ladder fills without placing or recording orders.

    This is an observation/readiness artifact for PolyDekos-style ladders. It
    fetches CLOB books through the same simulator as single-candidate checks,
    writes reproducible raw book snapshots, and marks the whole ladder
    executable only when every leg fully fills at the requested micro-size.
    """
    requested_usd = float(requested_usd_per_leg)
    legs_out: list[dict[str, Any]] = []
    rejection_reasons: list[str] = []
    total_cost = 0.0
    total_shares = 0.0
    total_requested = requested_usd * len(ladder.legs)
    max_leg_avg_price: float | None = None

    for leg in ladder.legs:
        fill = None
        snapshot_path = None
        snapshot_hash = None
        if leg.token_id:
            fill = clob.simulate_buy_fill(settings, str(leg.token_id), usd_size=requested_usd, max_avg_price=max_avg_price)
            snapshot_path, snapshot_hash = write_json_gz(
                settings.project_root,
                kind="ladder_books",
                name=f"{ladder.ladder_id[:24]}-{str(leg.token_id)[:16]}",
                payload=fill.book_payload,
                fetched_at=fill.book_fetched_at,
            )

        leg_reasons = _leg_rejection_reasons(leg, requested_usd, fill)
        rejection_reasons.extend(leg_reasons)
        leg_cost = float(fill.cost_usd) if fill is not None else 0.0
        leg_shares = float(fill.shares) if fill is not None else 0.0
        total_cost += leg_cost
        total_shares += leg_shares
        if fill is not None and fill.avg_price is not None:
            max_leg_avg_price = max(float(fill.avg_price), max_leg_avg_price or 0.0)

        legs_out.append(
            {
                "parent_ladder_id": ladder.ladder_id,
                "market_id": leg.market_id,
                "slug": leg.slug,
                "side": leg.side,
                "token_id": leg.token_id,
                "requested_usd": requested_usd,
                "filled": bool(fill.filled) if fill is not None else False,
                "best_bid": fill.best_bid if fill is not None else None,
                "best_ask": fill.best_ask if fill is not None else None,
                "avg_price": fill.avg_price if fill is not None else None,
                "shares": leg_shares,
                "cost_usd": leg_cost,
                "capacity_usd_at_best_ask": fill.capacity_usd_at_best_ask if fill is not None else None,
                "levels_used": fill.levels_used if fill is not None else [],
                "book_fetched_at": fill.book_fetched_at if fill is not None else None,
                "book_snapshot_path": snapshot_path,
                "book_snapshot_hash": snapshot_hash,
                "rejection_reasons": leg_reasons,
            }
        )

    all_legs_filled = bool(ladder.legs) and not rejection_reasons
    avg_price_weighted = total_cost / total_shares if total_shares > 0 else None
    return {
        "read_only": True,
        "no_orders_placed": True,
        "ladder_id": ladder.ladder_id,
        "requested_usd_per_leg": requested_usd,
        "total_requested_usd": round(total_requested, 6),
        "total_cost_usd": round(total_cost, 6),
        "total_shares": round(total_shares, 6),
        "avg_price_weighted": round(avg_price_weighted, 6) if avg_price_weighted is not None else None,
        "max_leg_avg_price": round(max_leg_avg_price, 6) if max_leg_avg_price is not None else None,
        "all_legs_filled": all_legs_filled,
        "executable": all_legs_filled,
        "rejection_reasons": rejection_reasons,
        "legs": legs_out,
    }
