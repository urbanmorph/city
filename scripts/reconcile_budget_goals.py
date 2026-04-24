#!/usr/bin/env python3
"""Reconcile tagged speech goals against function budgets, per corporation.

For each corp and each function, prints:
  - Budget (from lineitems JSON)
  - Goal count and sum of goal amounts (from tagged speech JSON)
  - Ratio of goal-sum to budget, as a sanity signal

Caveats:
  * Many goals have no explicit amount — they're announcements of policy
    or of a line already in the budget.
  * 03 Revenue goals are usually REVENUE *targets* (property tax, ad
    fees etc.), not expenditure. The "budget" column for 03 is the
    admin expenditure on running revenue operations, so the two
    numbers measure different things. High ratios there are expected.
  * Goals may carry multi-year commitments while the budget is annual.

A very high goal-sum vs budget (say >5x) for a non-Revenue function is
the strongest signal that a goal has been misclassified.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINEITEMS = ROOT / "data/bengaluru/budgets/2026-27/lineitems"
SPEECH = ROOT / "data/bengaluru/budgets/2026-27/speech"

CORPS = ["south", "central", "east", "west", "north"]

FUNCTIONS = [
    ("01", "Council"),
    ("02", "General Administration"),
    ("03", "Revenue"),
    ("04", "Town Planning"),
    ("05", "Public Works"),
    ("06", "Solid Waste Mgmt"),
    ("07", "Public Health-G"),
    ("08", "Public Health-M"),
    ("09", "Horticulture"),
    ("10", "Urban Forestry"),
    ("11", "Public Education"),
    ("12", "Social Welfare"),
]


def lakhs(v: float) -> str:
    if v == 0:
        return "—"
    return f"{v:>10,.0f}"


def flag(ratio: float | None) -> str:
    """Return a flag emoji-free tag for anomalous ratios."""
    if ratio is None:
        return ""
    if ratio > 5:
        return "HIGH"
    if ratio > 2:
        return "over"
    if ratio < 0.01:
        return "low"
    return ""


def reconcile_corp(corp: str) -> None:
    budget_path = LINEITEMS / f"{corp}.json"
    speech_path = SPEECH / f"{corp}-projects.tagged.json"
    if not budget_path.exists():
        print(f"\n=== {corp.upper()}: no lineitem data — skipped ===")
        return

    budget = json.loads(budget_path.read_text())
    budget_by_fn = {f["code"]: f["total_2026_27"] for f in budget["functions"]}

    goals_by_fn: dict[str, list[dict]] = {}
    if speech_path.exists():
        speech = json.loads(speech_path.read_text())
        for p in speech["projects"]:
            code = p.get("function_code") or "??"
            goals_by_fn.setdefault(code, []).append(p)

    total_budget = sum(budget_by_fn.values())
    total_goals = sum(
        p["amount_lakhs"] or 0
        for ps in goals_by_fn.values()
        for p in ps
    )

    print(f"\n=== {corp.upper()}  budget ₹{total_budget/100:,.0f} Cr  |  goal-sum ₹{total_goals/100:,.0f} Cr (where amounts specified) ===")
    print(
        f"  {'Fn':<4} {'Name':<18} {'Budget (L)':>11}   {'Goals':>5}  "
        f"{'w/amt':>5}  {'GoalSum (L)':>11}  {'Goal/Bud':>8}  flag"
    )
    print("  " + "-" * 84)

    for code, name in FUNCTIONS:
        bud = budget_by_fn.get(code, 0)
        goals = goals_by_fn.get(code, [])
        amounted = [g for g in goals if g.get("amount_lakhs") is not None]
        goal_sum = sum(g["amount_lakhs"] for g in amounted)
        if bud > 0 and goal_sum > 0:
            ratio = goal_sum / bud
            ratio_str = f"{ratio:6.1%}"
        elif goal_sum > 0:
            ratio = None
            ratio_str = "(no bud)"
        else:
            ratio = None
            ratio_str = "—"

        fl = flag(ratio)
        print(
            f"  {code:<4} {name:<18} {lakhs(bud)}   {len(goals):>5}  "
            f"{len(amounted):>5}  {lakhs(goal_sum)}  {ratio_str:>8}  {fl}"
        )


def main() -> None:
    for corp in CORPS:
        reconcile_corp(corp)
    print()
    print("Legend:")
    print("  Budget (L)   = function budget for 2026-27, in Rs lakhs")
    print("  Goals        = count of speech goals tagged to this function")
    print("  w/amt        = subset of Goals that carry an explicit amount")
    print("  GoalSum (L)  = sum of those amounts, Rs lakhs")
    print("  Goal/Bud     = ratio as % of budget")
    print("  flag 'HIGH'  = ratio > 500% (likely mis-tagged or multi-year / revenue target)")
    print("       'over'  = 200-500% (worth reviewing)")
    print("       'low'   = <1% (budget present but almost no goal amounts)")
    print()
    print("Note: 03 Revenue goals are revenue TARGETS (not expenditure) so high")
    print("ratios there are structural, not tagging errors.")


if __name__ == "__main__":
    main()
