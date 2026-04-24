"""Auto-tag budget speech projects with a function code (01-12).

Reads:  data/bengaluru/budgets/2026-27/speech/{corp}-projects.json
Writes: data/bengaluru/budgets/2026-27/speech/{corp}-projects.tagged.json

Usage:  python3 scripts/tag_speech_projects.py [--corp south]

The mapping is heuristic (category + keyword) and intentionally conservative.
Ambiguous items fall to "unmapped" so we can spot gaps on the page.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
# Each rule is (function_code, list_of_regex_patterns). Patterns are matched
# with re.IGNORECASE. Short generic words use \b word boundaries so e.g.
# "park" does NOT match "parking" and "lake" matches "lake" or "lakes".
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    # 08 Public Health-Medical (hospitals, clinics)
    ("08", [r"maternity hospital", r"\buphc\b", r"namma clinic", r"referral hospital",
            r"medical college", r"dialysis", r"mental health", r"palliative"]),
    # 07 Public Health-General (sanitation, vector, crematoria)
    ("07", [r"crematori(um|a)", r"burial ground", r"vector control", r"mosquito",
            r"sanitation worker", r"public toilet", r"pourakarmika"]),
    # 06 Solid Waste Management
    ("06", [r"solid waste", r"\bswm\b", r"\bbswml\b", r"garbage", r"compactor",
            r"dry waste", r"wet waste", r"waste to energy", r"landfill", r"segregation"]),
    # 12 Social Welfare (welfare, animal welfare, shelters, pensions, SCP/TSP)
    ("12", [r"dog shelter", r"abc centre", r"abc center", r"\bstray\b", r"cattle pound",
            r"animal welfare", r"\bpension\b", r"\bwidow\b", r"\bscp\b", r"\btsp\b", r"sc/st",
            r"differently abled", r"\borphan", r"women empowerment", r"self-help group",
            r"\bshg\b"]),
    # 11 Public Education
    ("11", [r"\bschools?\b", r"\blibrar(y|ies)\b", r"scholarship", r"coaching",
            r"\bstudents?\b", r"university research internship", r"skill development"]),
    # 03 Revenue — includes parking-as-revenue; must run BEFORE 09 so "parking"
    # gets tagged here (rather than matching "park" in 09 Horticulture)
    ("03", [
        r"property tax", r"tax collection", r"tax[- ]evasion", r"advertisement polic(y|ies)",
        r"advertisements on", r"\bb-khata\b", r"\be-khata\b", r"\bkhata\b", r"premium far",
        r"single plot approval", r"building plan", r"occupancy certificate",
        r"revenue grant", r"revenue\s*/\s*resource", r"\bparking\b",
        r"land acquisition dispute", r"fee revenue",
        r"\badvertisement tender",
        # North-specific revenue patterns (do not match any South project — verified)
        r"stamp duty", r"\bpsus?\b", r"development charges",
        r"b[- ]register", r"\ba-khatha\b", r"\bb-khatha\b",
        r"advertisement\S?\)?\s*rules", r"advertisement revenue",
        r"license fees", r"vacant municipal", r"urban design cell",
        r"property records", r"\be-khatha\b", r"greater bengaluru\s*(?:area)?\s*\(advertisement\)",
    ]),
    # 10 Urban Forestry — before 09 so tree/forestry items don't fall into parks
    ("10", [r"wildlife", r"environment day", r"forestry", r"afforest", r"tree planting",
            r"biodiversity", r"\bsaplings?\b", r"tree[- ]branch", r"memorial forest"]),
    # 09 Horticulture (parks, lakes, gardens) — parks with word boundary; dropped
    # overly generic "open space" which was catching beautification projects
    ("09", [r"\blakes?\b", r"kere mitra", r"\bparks?\b", r"\bgardens?\b", r"\bplaygrounds?\b"]),
    # 04 Town Planning and Regulation
    ("04", [r"master plan", r"comprehensive master plan", r"zoning", r"layout approval",
            r"town planning", r"\btdr cell\b", r"\btdr exchange\b",
            r"single window approval", r"digiti(z|s)e the identification of land"]),
    # 05 Public Works (roads, drains, bridges, lighting, buildings, water, beautification)
    ("05", [
        r"\broads?\b", r"junction", r"flyover", r"underpass", r"bridge", r"footpath",
        r"pavement", r"stormwater", r"storm water", r"\bdrains?\b", r"drainage",
        r"water supply", r"borewell", r"street light", r"\bled\b", r"\bpoles?\b",
        r"\bbuildings?\b", r"headquarters", r"skywalk", r"\bcmidp\b", r"\bsip\b",
        r"beautif", r"fountain", r"public space improvement", r"\bbsmile\b",
        r"mobility", r"\btraffic\b", r"\bwells?\b",
    ]),
    # 02 General Administration (governance, IT, staff, vigilance, disaster mgmt, outreach)
    ("02", [
        r"ward office", r"e-office", r"paperless", r"\bstaff\b", r"training programme",
        r"attendance management", r"vigilance", r"expert committee", r"\bifms\b",
        r"internal audit", r"bscc dedicated website", r"\bwebsites?\b", r"social media",
        r"citizen outreach", r"janara kade", r"review meetings", r"disaster management",
        r"ai-enabled", r"ai platform", r"research internship",
        r"information technology cell", r"integrated command",
        r"news bulletin", r"ev charging",
        # North-specific administrative patterns (do not match any South project — verified)
        r"\baudit cell\b", r"records digiti[sz]ation", r"\bcomplaint\b",
        r"phone-in", r"\btrade licen[cs]e", r"host-to-host", r"online payment",
        r"online building plan", r"\bobps\b", r"dishank", r"biometric attendance",
        r"ease of doing business", r"inter-department clearance",
        r"e-drc", r"e-tdr", r"administration software",
        r"software to facilitate the administration",
    ]),
    # 01 Council (elected reps — rarely seen in speech but possible)
    ("01", [r"corporator", r"council meeting", r"standing committee"]),
]

KEYWORD_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    (code, [re.compile(p, re.IGNORECASE) for p in patterns])
    for code, patterns in KEYWORD_RULES
]

CATEGORY_RAW_KEY = "category"  # existing key in source JSON


def suggest_function(project: dict) -> tuple[str | None, str]:
    """Return (function_code, reason) or (None, reason) if unmapped."""
    name = project.get("name", "")
    desc = project.get("description") or ""
    verbatim = project.get("verbatim_quote") or ""
    hay = f"{name} {desc} {verbatim}"

    # Keyword rules take precedence (more specific than category)
    for code, patterns in KEYWORD_PATTERNS:
        for pat in patterns:
            m = pat.search(hay)
            if m:
                return code, f"keyword: {m.group(0).lower()!r}"

    # Fallback to category mapping
    cat = project.get(CATEGORY_RAW_KEY)
    if cat and cat in CATEGORY_MAP:
        return CATEGORY_MAP[cat], f"category: {cat}"

    return None, "no match"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corp", default="south", help="corporation id (e.g. south, central)")
    args = ap.parse_args()

    SRC = ROOT / f"data/bengaluru/budgets/2026-27/speech/{args.corp}-projects.json"
    OUT = ROOT / f"data/bengaluru/budgets/2026-27/speech/{args.corp}-projects.tagged.json"

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
