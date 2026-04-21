"""Auto-tag budget speech projects with a function code (01-12).

Reads:  data/bengaluru/budgets/2026-27/speech/south-projects.json
Writes: data/bengaluru/budgets/2026-27/speech/south-projects.tagged.json

The mapping is heuristic (category + keyword) and intentionally conservative.
Ambiguous items fall to "unmapped" so we can spot gaps on the page.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/bengaluru/budgets/2026-27/speech/south-projects.json"
OUT = ROOT / "data/bengaluru/budgets/2026-27/speech/south-projects.tagged.json"

FUNCTIONS = {
    "01": "Council",
    "02": "General Administration",
    "03": "Revenue",
    "04": "Town Planning and Regulation",
    "05": "Public Works",
    "06": "Solid Waste Management",
    "07": "Public Health-General",
    "08": "Public Health-Medical",
    "09": "Horticulture",
    "10": "Urban Forestry",
    "11": "Public Education",
    "12": "Social Welfare",
}

# Category → function (first pass)
CATEGORY_MAP = {
    "welfare": "12",
    "education": "11",
    "swm": "06",
    "parks": "09",
    "roads": "05",
    "water": "05",
    "lighting": "05",
    "infrastructure": "05",
    "health": "07",  # refined below via keywords
}

# Keyword rules — run in order; first match wins.
# Each rule is (function_code, list_of_substrings_to_match_lower).
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    # 08 Public Health-Medical (hospitals, clinics)
    ("08", ["maternity hospital", "uphc", "namma clinic", "referral hospital", "medical college", "dialysis", "mental health", "palliative"]),
    # 07 Public Health-General (sanitation, vector, crematoria)
    ("07", ["crematorium", "burial ground", "vector control", "mosquito", "sanitation worker", "public toilet", "pourakarmika"]),
    # 06 Solid Waste Management
    ("06", ["solid waste", "swm", "bswml", "garbage", "compactor", "dry waste", "wet waste", "waste to energy", "landfill", "segregation"]),
    # 12 Social Welfare (welfare, animal welfare, shelters, pensions, SCP/TSP)
    ("12", ["dog shelter", "abc centre", "abc center", "stray", "cattle pound", "animal welfare", "pension", "widow", "scp", "tsp", "sc/st", "differently abled", "orphan", "women empowerment", "self-help group", "shg"]),
    # 11 Public Education
    ("11", ["school", "library", "scholarship", "coaching", "student ", "university research internship", "skill development"]),
    # 10 Urban Forestry
    ("10", ["wildlife", "environment day", "forestry", "afforest", "tree planting", "biodiversity"]),
    # 09 Horticulture (parks, lakes, gardens)
    ("09", ["lake", "kere mitra", "park", "garden", "tree park", "open space", "playground"]),
    # 03 Revenue
    ("03", [
        "property tax", "tax collection", "tax evasion", "tax-evasion", "advertisement policy",
        "advertisements on", "b-khata", "e-khata", "khata", "premium far", "single plot approval",
        "building plan", "occupancy certificate", "revenue grant", "revenue / resource",
        "revenue grant", "parking", "land acquisition dispute", "fee revenue",
    ]),
    # 04 Town Planning and Regulation
    ("04", ["master plan", "comprehensive master plan", "zoning", "layout approval", "town planning"]),
    # 05 Public Works (roads, drains, bridges, lighting, buildings, water, beautification)
    ("05", [
        "road", "junction", "flyover", "underpass", "bridge", "footpath", "pavement",
        "stormwater", "storm water", "drain", "drainage", "water supply", "borewell",
        "street light", "led", "pole", "building", "headquarters", "skywalk",
        "cmidp", "sip", "beautif", "fountain", "public space improvement", "bsmile",
        "mobility", "traffic",
    ]),
    # 02 General Administration (governance, IT, staff, vigilance, disaster mgmt, outreach)
    ("02", [
        "ward office", "e-office", "paperless", "staff", "training programme",
        "attendance management", "vigilance", "expert committee", "ifms", "internal audit",
        "bscc dedicated website", "website", "social media", "citizen outreach",
        "janara kade", "review meetings", "disaster management", "ai-enabled",
        "ai platform", "research internship",
    ]),
    # 01 Council (elected reps — rarely seen in speech but possible)
    ("01", ["corporator", "council meeting", "standing committee"]),
]

CATEGORY_RAW_KEY = "category"  # existing key in source JSON


def suggest_function(project: dict) -> tuple[str | None, str]:
    """Return (function_code, reason) or (None, reason) if unmapped."""
    name = project.get("name", "").lower()
    desc = (project.get("description") or "").lower()
    verbatim = (project.get("verbatim_quote") or "").lower()
    hay = f"{name} {desc} {verbatim}"

    # Keyword rules take precedence (more specific than category)
    for code, kws in KEYWORD_RULES:
        for kw in kws:
            if kw in hay:
                return code, f"keyword: {kw!r}"

    # Fallback to category mapping
    cat = project.get(CATEGORY_RAW_KEY)
    if cat and cat in CATEGORY_MAP:
        return CATEGORY_MAP[cat], f"category: {cat}"

    return None, "no match"


def main() -> None:
    data = json.loads(SRC.read_text())
    projects = data["projects"]

    by_fn: dict[str, int] = {}
    unmapped: list[str] = []

    for p in projects:
        code, reason = suggest_function(p)
        p["function_code"] = code
        p["function_name"] = FUNCTIONS.get(code) if code else None
        p["tag_reason"] = reason
        if code:
            by_fn[code] = by_fn.get(code, 0) + 1
        else:
            unmapped.append(p["name"])

    # Preserve speech-order but also surface function-grouped counts
    data["functions_summary"] = [
        {
            "code": code,
            "name": FUNCTIONS[code],
            "project_count": by_fn.get(code, 0),
        }
        for code in FUNCTIONS
    ]
    data["unmapped_count"] = len(unmapped)

    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"Total projects: {len(projects)}")
    print("Per-function counts:")
    for code in FUNCTIONS:
        print(f"  {code} {FUNCTIONS[code]:30s} {by_fn.get(code, 0):3d}")
    print(f"Unmapped: {len(unmapped)}")
    for name in unmapped:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
