#!/usr/bin/env python3
"""Compute usage-scenario unit economics for metered-AI subscription tiers.

Encodes the arithmetic pattern used repeatedly when designing tiers for a
product whose marginal cost comes from metered API calls (LLM tokens, image
generations, video-seconds, etc.): for each paid tier, model a light/average/
heavy usage scenario, then roll everything into a portfolio projection and a
breakeven free->paid conversion rate.

Input is a single JSON spec (see --example for the shape). Output is a
human-readable table on stdout, plus the full computed result as JSON on
stderr-free stdout when --json is passed (useful for feeding numbers into a
report template).

Usage:
    python tier_economics.py spec.json
    python tier_economics.py spec.json --json > result.json
    python tier_economics.py --example > spec.json   # scaffold a starting spec
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EXAMPLE_SPEC = {
    "currency": "USD",
    "cost_per_unit": {"text": 0.0001, "photo": 0.04, "video": 0.96},
    "payment_fee": {"pct": 2.9, "flat": 0.30},
    "scenario_fractions": {"light": 0.30, "average": 0.50, "heavy": 1.0},
    "free_tier": {"limits": {"text": 20, "photo": 3, "video": 0}},
    "paid_tiers": [
        {"name": "Pro", "price": 19, "limits": {"text": 300, "photo": 60, "video": 6}},
        {"name": "Business", "price": 100, "limits": {"text": 600, "photo": 150, "video": 55}},
    ],
    "portfolio": {
        "total_users": 500,
        "conversion_rate": 0.04,
        "tier_mix": {"Pro": 0.80, "Business": 0.20},
        "scenario": "average",
    },
}


def payment_fee(price: float, fee_spec: dict[str, float]) -> float:
    return round(price * fee_spec.get("pct", 0) / 100 + fee_spec.get("flat", 0), 4)


def free_tier_cost(free_tier: dict[str, Any], cost_per_unit: dict[str, float]) -> float:
    return round(
        sum(free_tier["limits"].get(fmt, 0) * cost for fmt, cost in cost_per_unit.items()), 6
    )


def tier_scenarios(
    tier: dict[str, Any], cost_per_unit: dict[str, float], fee_spec: dict[str, float],
    fractions: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """One scenario per fraction (e.g. light=30%, average=50%, heavy=100%
    of the tier's limits) -- a single "average user" number hides the fact
    that a tier must stay solvent even for the users who use every bit of
    what they're paying for.
    """
    price = tier["price"]
    fee = payment_fee(price, fee_spec)
    results: dict[str, dict[str, Any]] = {}
    for scenario_name, frac in fractions.items():
        breakdown = {
            fmt: round(tier["limits"].get(fmt, 0) * frac * cost, 6)
            for fmt, cost in cost_per_unit.items()
        }
        ai_cost = round(sum(breakdown.values()), 6)
        net = round(price - ai_cost - fee, 4)
        margin = round(net / price * 100, 1) if price else 0.0
        results[scenario_name] = {
            "fraction_of_limit": frac,
            "units_used": {fmt: round(tier["limits"].get(fmt, 0) * frac, 1) for fmt in cost_per_unit},
            "cost_breakdown": breakdown,
            "ai_cost": ai_cost,
            "payment_fee": fee,
            "net_profit": net,
            "margin_pct": margin,
        }
    return results


def portfolio_projection(spec: dict[str, Any], all_tier_scenarios: dict[str, dict]) -> dict[str, Any]:
    portfolio = spec.get("portfolio")
    if not portfolio:
        return {}
    total = portfolio["total_users"]
    conv = portfolio["conversion_rate"]
    paying = round(total * conv)
    free_n = total - paying
    scenario = portfolio.get("scenario", "average")

    free_cost = round(free_n * free_tier_cost(spec["free_tier"], spec["cost_per_unit"]), 4)

    tier_breakdown = {}
    total_paid_net = 0.0
    for tier_name, share in portfolio["tier_mix"].items():
        n = round(paying * share)
        net_per_user = all_tier_scenarios[tier_name][scenario]["net_profit"]
        tier_net = round(n * net_per_user, 4)
        tier_breakdown[tier_name] = {"users": n, "net_profit_per_user": net_per_user, "total_net": tier_net}
        total_paid_net += tier_net

    net_result = round(total_paid_net - free_cost, 4)
    return {
        "total_users": total,
        "free_users": free_n,
        "paying_users": paying,
        "free_cost_total": free_cost,
        "tiers": tier_breakdown,
        "net_result": net_result,
    }


def breakeven_conversion(
    spec: dict[str, Any], all_tier_scenarios: dict[str, dict], scenario: str = "average"
) -> dict[str, Any]:
    """For each paid tier: how many free users does one subscriber's net
    profit offset, and what conversion rate does that imply at breakeven?
    Cheap sanity check on how much margin-of-safety a target conversion
    rate (e.g. from a product's success metrics) actually has.
    """
    free_cost = free_tier_cost(spec["free_tier"], spec["cost_per_unit"])
    out = {}
    for tier in spec["paid_tiers"]:
        net = all_tier_scenarios[tier["name"]][scenario]["net_profit"]
        if free_cost <= 0 or net <= 0:
            out[tier["name"]] = {"free_users_offset": None, "breakeven_conversion_pct": None}
            continue
        ratio = net / free_cost
        breakeven_pct = round(1 / (ratio + 1) * 100, 3)
        out[tier["name"]] = {
            "free_users_offset_by_one_subscriber": round(ratio, 1),
            "breakeven_conversion_pct": breakeven_pct,
        }
    return out


def render_table(result: dict[str, Any]) -> str:
    lines = []
    cur = result["currency"]
    lines.append("=== Себестоимость / unit ===")
    for fmt, cost in result["cost_per_unit"].items():
        lines.append(f"  {fmt:10s} {cur} {cost:.6f}")

    lines.append("")
    lines.append(f"=== Free tier: {cur} {result['free_tier_cost']:.4f}/пользователь/мес ===")

    for tier_name, scenarios in result["tiers"].items():
        price = next(t["price"] for t in result["_spec"]["paid_tiers"] if t["name"] == tier_name)
        lines.append("")
        lines.append(f"=== {tier_name} — {cur} {price}/мес ===")
        for scenario_name, s in scenarios.items():
            lines.append(
                f"  {scenario_name:10s} ({s['fraction_of_limit']:.0%} лимита)  "
                f"ai_cost={s['ai_cost']:7.2f}  fee={s['payment_fee']:5.2f}  "
                f"net={s['net_profit']:8.2f}  margin={s['margin_pct']:5.1f}%"
            )

    if result.get("portfolio"):
        p = result["portfolio"]
        lines.append("")
        lines.append(
            f"=== Портфель: {p['total_users']} юзеров, {p['paying_users']} платящих "
            f"({p['free_users']} free) ==="
        )
        lines.append(f"  Free cost total:  -{p['free_cost_total']:.2f}")
        for tier_name, t in p["tiers"].items():
            lines.append(f"  {tier_name} ({t['users']} юзеров): +{t['total_net']:.2f}")
        lines.append(f"  Net result:        {p['net_result']:.2f}")

    if result.get("breakeven"):
        lines.append("")
        lines.append("=== Точка безубыточности (по AI-части) ===")
        for tier_name, b in result["breakeven"].items():
            if b["breakeven_conversion_pct"] is None:
                lines.append(f"  {tier_name}: н/д (нулевая или отрицательная прибыль в этом сценарии)")
            else:
                lines.append(
                    f"  {tier_name}: 1 подписчик окупает ~{b['free_users_offset_by_one_subscriber']:.0f} "
                    f"free-пользователей -> breakeven conversion ~{b['breakeven_conversion_pct']:.2f}%"
                )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", nargs="?", help="Path to the JSON spec file")
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON instead of a table")
    parser.add_argument("--example", action="store_true", help="Print an example spec and exit")
    args = parser.parse_args()

    if args.example:
        print(json.dumps(EXAMPLE_SPEC, indent=2, ensure_ascii=False))
        return

    if not args.spec:
        parser.error("spec file required (or pass --example to scaffold one)")

    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)

    fractions = spec.get("scenario_fractions", {"light": 0.3, "average": 0.5, "heavy": 1.0})

    all_tier_scenarios = {
        tier["name"]: tier_scenarios(tier, spec["cost_per_unit"], spec["payment_fee"], fractions)
        for tier in spec["paid_tiers"]
    }

    result: dict[str, Any] = {
        "currency": spec.get("currency", "USD"),
        "cost_per_unit": spec["cost_per_unit"],
        "free_tier_cost": free_tier_cost(spec["free_tier"], spec["cost_per_unit"]),
        "tiers": all_tier_scenarios,
        "_spec": spec,
    }
    if spec.get("portfolio"):
        result["portfolio"] = portfolio_projection(spec, all_tier_scenarios)
        result["breakeven"] = breakeven_conversion(spec, all_tier_scenarios, spec["portfolio"].get("scenario", "average"))

    if args.json:
        result.pop("_spec")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_table(result))


if __name__ == "__main__":
    main()
