#!/usr/bin/env python3
"""
sync_grievances.py — Pull aggregated complaint data from the notf-cms Supabase
database and write per-corporation JSON files for the city governance portal.

Usage:
    python3 scripts/sync_grievances.py
    python3 scripts/sync_grievances.py -c south
    python3 scripts/sync_grievances.py --dry-run
    python3 scripts/sync_grievances.py -o /tmp/grievances --corporation central
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default Supabase connection
# ---------------------------------------------------------------------------
DEFAULT_SUPABASE_URL = "https://abblyaukkoxmgzwretvm.supabase.co"
DEFAULT_SUPABASE_KEY = "sb_publishable_I1nVJvhGbNwAgSiypEq1gg_KkMaKtar"

# Bengaluru GBA corporation codes
BENGALURU_CORPS = ["central", "south", "east", "west", "north"]

# Statuses that count as "closed" (resolved or closed)
CLOSED_STATUSES = {"resolved", "closed"}

# Page size for Supabase pagination (max 1000)
PAGE_SIZE = 1000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env(project_root: Path) -> None:
    """Load .env from project root if python-dotenv is available."""
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        # Manually parse simple KEY=VALUE lines
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                os.environ.setdefault(key, value)


def get_supabase_client(url: str, key: str):
    """Create and return a Supabase client, or None on failure."""
    try:
        from supabase import create_client
        client = create_client(url, key)
        return client
    except ImportError:
        print("ERROR: supabase-py is not installed. Run: pip install supabase", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"ERROR: Failed to connect to Supabase: {exc}", file=sys.stderr)
        return None


def paginated_select(query_builder, page_size: int = PAGE_SIZE) -> list[dict]:
    """
    Fetch all rows from a Supabase query builder using range-based pagination.

    The query builder is the result of client.table(...).select(...) with any
    filters already applied.  We call .range() and .execute() on it.  The
    supabase-py client replaces (not appends) the range on each call, so
    reusing the same builder is safe.
    """
    all_rows: list[dict] = []
    offset = 0
    while True:
        resp = query_builder.range(offset, offset + page_size - 1).execute()
        rows = resp.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def load_function_mapping(project_root: Path) -> dict:
    """Load function-mapping.json and build lookup structures.

    Returns a dict with:
      - "functions": list of function defs
      - "dept_to_fn": {parent_category_code -> function_id}
      - "cat_to_fn":  {child_category_code -> function_id}
    """
    mapping_path = project_root / "data" / "bengaluru" / "function-mapping.json"
    with open(mapping_path) as f:
        data = json.load(f)

    dept_to_fn: dict[str, str] = {}
    cat_to_fn: dict[str, str] = {}

    for func in data["functions"]:
        fn_id = func["id"]
        for dept_code in func.get("notf_cms_departments", []):
            dept_to_fn[dept_code] = fn_id
        for cat_code in func.get("notf_cms_categories", []):
            cat_to_fn[cat_code] = fn_id

    return {
        "functions": data["functions"],
        "dept_to_fn": dept_to_fn,
        "cat_to_fn": cat_to_fn,
    }


def round_rate(numerator: int, denominator: int, decimals: int = 2) -> float:
    """Safely compute a percentage rate."""
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, decimals)


def round_share(numerator: int, denominator: int, decimals: int = 3) -> float:
    """Safely compute a share (0-1)."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, decimals)


# ---------------------------------------------------------------------------
# Core data-fetching logic
# ---------------------------------------------------------------------------

def fetch_corporation_uuid(client, corp_code: str) -> str | None:
    """Look up a corporation's UUID by its code."""
    resp = (
        client.table("corporations")
        .select("id")
        .eq("code", corp_code)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    return rows[0]["id"]


def fetch_parent_category_lookup(client) -> dict[str, str]:
    """Build a map of {category_id -> parent_category_code}.

    For top-level categories (parent_id is null), the code maps to itself.
    For child categories, the code maps to the parent's code.
    """
    # Fetch all categories
    all_cats = paginated_select(
        client.table("issue_categories").select("id, code, parent_id")
    )

    id_to_code: dict[str, str] = {}
    id_to_parent: dict[str, str | None] = {}

    for cat in all_cats:
        id_to_code[cat["id"]] = cat["code"]
        id_to_parent[cat["id"]] = cat["parent_id"]

    # Resolve: for each category, find its top-level parent code
    cat_id_to_parent_code: dict[str, str] = {}
    for cat_id, code in id_to_code.items():
        parent_id = id_to_parent.get(cat_id)
        if parent_id is None:
            # This IS a top-level category
            cat_id_to_parent_code[cat_id] = code
        else:
            # Walk up (one level is enough given the schema)
            cat_id_to_parent_code[cat_id] = id_to_code.get(parent_id, code)

    return cat_id_to_parent_code


def fetch_ward_name_lookup(client) -> dict[str, str]:
    """Build a map of {ward_id -> ward_name}."""
    all_wards = paginated_select(
        client.table("wards").select("id, name")
    )
    return {w["id"]: w["name"] for w in all_wards}


def fetch_complaints_for_corp(client, corp_uuid: str) -> list[dict]:
    """Fetch all complaints for a given corporation UUID with pagination."""
    return paginated_select(
        client.table("complaints")
        .select("id, status, category_id, ward_id, created_at, resolved_at")
        .eq("corporation_id", corp_uuid)
    )


def compute_avg_response_time(complaints: list[dict]) -> str:
    """Compute average resolution time for resolved/closed complaints.

    Returns a human-readable string like '21 hours' or '3 days'.
    """
    total_seconds = 0
    count = 0
    for c in complaints:
        if c.get("status") in CLOSED_STATUSES and c.get("resolved_at") and c.get("created_at"):
            try:
                created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
                resolved = datetime.fromisoformat(c["resolved_at"].replace("Z", "+00:00"))
                delta = (resolved - created).total_seconds()
                if delta > 0:
                    total_seconds += delta
                    count += 1
            except (ValueError, TypeError):
                continue

    if count == 0:
        return "N/A"

    avg_seconds = total_seconds / count
    avg_hours = avg_seconds / 3600

    if avg_hours < 1:
        return f"{int(avg_seconds / 60)} minutes"
    elif avg_hours < 48:
        return f"{int(round(avg_hours))} hours"
    else:
        return f"{int(round(avg_hours / 24))} days"


def aggregate_corporation(
    complaints: list[dict],
    corp_code: str,
    cat_id_to_parent_code: dict[str, str],
    ward_id_to_name: dict[str, str],
    fn_mapping: dict,
) -> dict[str, Any]:
    """Aggregate complaints into the target JSON structure."""

    total = len(complaints)
    closed = sum(1 for c in complaints if c.get("status") in CLOSED_STATUSES)
    open_count = total - closed

    # --- Group by parent category code ---
    dept_counts: dict[str, dict] = {}  # parent_code -> {"total": N, "closed": N}
    for c in complaints:
        cat_id = c.get("category_id")
        if not cat_id:
            parent_code = "_unknown"
        else:
            parent_code = cat_id_to_parent_code.get(cat_id, "_unknown")

        if parent_code not in dept_counts:
            dept_counts[parent_code] = {"total": 0, "closed": 0}
        dept_counts[parent_code]["total"] += 1
        if c.get("status") in CLOSED_STATUSES:
            dept_counts[parent_code]["closed"] += 1

    # --- Map departments to functions ---
    dept_to_fn = fn_mapping["dept_to_fn"]
    # Also build reverse: for parent codes that don't match dept codes,
    # check if any child category code matches
    cat_to_fn = fn_mapping["cat_to_fn"]

    fn_counts: dict[str, dict] = {}  # function_id -> {"total": N, "closed": N}
    unmapped_depts: set[str] = set()

    for parent_code, counts in dept_counts.items():
        fn_id = dept_to_fn.get(parent_code)
        if fn_id is None:
            # Try matching as a child category code
            fn_id = cat_to_fn.get(parent_code)
        if fn_id is None:
            if parent_code != "_unknown":
                unmapped_depts.add(parent_code)
            continue

        if fn_id not in fn_counts:
            fn_counts[fn_id] = {"total": 0, "closed": 0}
        fn_counts[fn_id]["total"] += counts["total"]
        fn_counts[fn_id]["closed"] += counts["closed"]

    by_function: dict[str, dict] = {}
    for fn_id, counts in sorted(fn_counts.items(), key=lambda x: -x[1]["total"]):
        by_function[fn_id] = {
            "count": counts["total"],
            "share": round_share(counts["total"], total),
            "resolution_rate": round_rate(counts["closed"], counts["total"]),
        }

    # --- Top wards ---
    ward_complaint_counts: dict[str, int] = {}
    for c in complaints:
        ward_id = c.get("ward_id")
        if ward_id:
            ward_complaint_counts[ward_id] = ward_complaint_counts.get(ward_id, 0) + 1

    top_ward_ids = sorted(ward_complaint_counts, key=ward_complaint_counts.get, reverse=True)[:5]
    top_wards = [ward_id_to_name.get(wid, wid) for wid in top_ward_ids]

    # --- Average response time ---
    avg_response = compute_avg_response_time(complaints)

    result = {
        "corporation_id": corp_code,
        "source": "notf-cms",
        "total_complaints": total,
        "closed": closed,
        "open": open_count,
        "resolution_rate": round_rate(closed, total),
        "avg_response_time": avg_response,
        "by_function": by_function,
        "top_wards": top_wards,
    }

    return result, unmapped_depts


# ---------------------------------------------------------------------------
# Dry-run helpers
# ---------------------------------------------------------------------------

def print_dry_run(corp_codes: list[str], supabase_url: str, output_dir: Path):
    """Print the queries that would be executed without actually running them."""
    print("=" * 60)
    print("DRY RUN -- no data will be fetched or written")
    print("=" * 60)
    print(f"\nSupabase URL : {supabase_url}")
    print(f"Output dir   : {output_dir}")
    print(f"Corporations : {', '.join(corp_codes)}")
    print()

    print("The following queries would be executed:\n")

    print("  1. Fetch corporation UUIDs:")
    print(f"     SELECT id, code FROM corporations WHERE code IN ({', '.join(repr(c) for c in corp_codes)})")
    print()

    print("  2. Fetch all issue categories (for parent-code lookup):")
    print("     SELECT id, code, parent_id FROM issue_categories")
    print()

    print("  3. Fetch all wards (for name lookup):")
    print("     SELECT id, name FROM wards")
    print()

    for corp in corp_codes:
        print(f"  4. [{corp}] Fetch complaints with pagination (page_size={PAGE_SIZE}):")
        print(f"     SELECT id, status, category_id, ward_id, created_at, resolved_at")
        print(f"       FROM complaints WHERE corporation_id = <{corp}_uuid>")
        print()

    print("  5. For each corporation, aggregate:")
    print("     - Total / closed / open counts")
    print("     - Group by parent category -> map to function IDs via function-mapping.json")
    print("     - Top 5 wards by complaint volume")
    print("     - Average response time from created_at to resolved_at")
    print()

    for corp in corp_codes:
        print(f"  6. Write: {output_dir / f'{corp}.json'}")

    print()
    print("=" * 60)
    print("End of dry run. Re-run without --dry-run to execute.")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    project_root = Path(__file__).resolve().parent.parent
    load_env(project_root)

    parser = argparse.ArgumentParser(
        description="Sync grievance data from notf-cms Supabase into per-corporation JSON files."
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=project_root / "data" / "bengaluru" / "grievances",
        help="Output directory for JSON files (default: data/bengaluru/grievances/)",
    )
    parser.add_argument(
        "-c", "--corporation",
        type=str,
        default=None,
        help="Sync only this corporation code (default: all 5 Bengaluru corps)",
    )
    parser.add_argument(
        "--supabase-url",
        type=str,
        default=None,
        help="Override Supabase project URL",
    )
    parser.add_argument(
        "--supabase-key",
        type=str,
        default=None,
        help="Override Supabase anon key",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what queries would be run without executing them",
    )
    args = parser.parse_args()

    # Resolve Supabase credentials: CLI arg > env var > default
    supabase_url = (
        args.supabase_url
        or os.environ.get("SUPABASE_URL")
        or DEFAULT_SUPABASE_URL
    )
    supabase_key = (
        args.supabase_key
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or DEFAULT_SUPABASE_KEY
    )

    # Which corporations to sync
    if args.corporation:
        corp_codes = [args.corporation]
    else:
        corp_codes = list(BENGALURU_CORPS)

    output_dir: Path = args.output_dir

    # --- Dry run ---
    if args.dry_run:
        print_dry_run(corp_codes, supabase_url, output_dir)
        return

    # --- Real run ---
    print(f"Connecting to Supabase at {supabase_url} ...")
    client = get_supabase_client(supabase_url, supabase_key)
    if client is None:
        print(
            "\nFallback: Could not connect to Supabase. Possible causes:\n"
            "  - Invalid URL or anon key\n"
            "  - Network issue\n"
            "  - supabase-py not installed (pip install supabase)\n"
            "\nTo override credentials, use --supabase-url and --supabase-key,\n"
            "or set SUPABASE_URL / SUPABASE_KEY in a .env file at the project root.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load function mapping
    print("Loading function mapping ...")
    try:
        fn_mapping = load_function_mapping(project_root)
    except FileNotFoundError:
        print(
            "ERROR: function-mapping.json not found at "
            f"{project_root / 'data' / 'bengaluru' / 'function-mapping.json'}",
            file=sys.stderr,
        )
        sys.exit(1)

    mapped_fn_ids = set(fn_mapping["dept_to_fn"].values()) | set(fn_mapping["cat_to_fn"].values())
    print(f"  {len(fn_mapping['dept_to_fn'])} department codes -> {len(mapped_fn_ids)} functions")
    print(f"  {len(fn_mapping['cat_to_fn'])} category codes mapped")

    # Fetch shared lookup tables
    print("Fetching issue category hierarchy ...")
    try:
        cat_id_to_parent_code = fetch_parent_category_lookup(client)
        print(f"  {len(cat_id_to_parent_code)} categories loaded")
    except Exception as exc:
        print(f"ERROR: Failed to fetch issue categories: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Fetching ward names ...")
    try:
        ward_id_to_name = fetch_ward_name_lookup(client)
        print(f"  {len(ward_id_to_name)} wards loaded")
    except Exception as exc:
        print(f"WARNING: Failed to fetch wards (top_wards will use IDs): {exc}", file=sys.stderr)
        ward_id_to_name = {}

    # Resolve corporation UUIDs
    print("Resolving corporation UUIDs ...")
    corp_uuids: dict[str, str] = {}
    for code in corp_codes:
        uuid = fetch_corporation_uuid(client, code)
        if uuid is None:
            print(f"  WARNING: Corporation '{code}' not found in database, skipping.", file=sys.stderr)
        else:
            corp_uuids[code] = uuid
            print(f"  {code} -> {uuid}")

    if not corp_uuids:
        print("ERROR: No valid corporations found. Nothing to sync.", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Process each corporation
    print()
    all_unmapped: set[str] = set()
    summary: list[dict] = []

    for corp_code, corp_uuid in corp_uuids.items():
        print(f"--- {corp_code.upper()} ---")

        print(f"  Fetching complaints ...")
        try:
            complaints = fetch_complaints_for_corp(client, corp_uuid)
        except Exception as exc:
            print(f"  ERROR: Failed to fetch complaints: {exc}", file=sys.stderr)
            print(
                "  NOTE: If the error is about corporation_id, the complaints table\n"
                "  may require joining through wards or zones. Check the schema and\n"
                "  adjust fetch_complaints_for_corp() to join via:\n"
                "    complaints -> ward_id -> wards -> zone_id -> zones -> corporation_id",
                file=sys.stderr,
            )
            continue

        print(f"  {len(complaints)} complaints fetched")

        if len(complaints) == 0:
            print(f"  No complaints for {corp_code}, writing empty shell.")

        result, unmapped = aggregate_corporation(
            complaints, corp_code, cat_id_to_parent_code, ward_id_to_name, fn_mapping
        )
        all_unmapped.update(unmapped)

        # Write JSON
        out_path = output_dir / f"{corp_code}.json"
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  Written to {out_path}")

        summary.append({
            "corporation": corp_code,
            "total": result["total_complaints"],
            "closed": result["closed"],
            "open": result["open"],
            "rate": result["resolution_rate"],
            "functions": len(result["by_function"]),
        })

    # Summary
    print()
    print("=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    print(f"{'Corp':<12} {'Total':>8} {'Closed':>8} {'Open':>8} {'Rate':>8} {'Functions':>10}")
    print("-" * 60)
    for s in summary:
        print(
            f"{s['corporation']:<12} {s['total']:>8} {s['closed']:>8} "
            f"{s['open']:>8} {s['rate']:>7.1f}% {s['functions']:>10}"
        )
    print("-" * 60)

    # Function mapping coverage
    if all_unmapped:
        print(f"\nUnmapped parent categories (not in function-mapping.json):")
        for code in sorted(all_unmapped):
            print(f"  - {code}")
        print(
            "\nTo map these, add their codes to notf_cms_departments or\n"
            "notf_cms_categories in data/bengaluru/function-mapping.json"
        )
    else:
        print("\nAll complaint categories mapped to functions successfully.")

    print(f"\nDone. Output: {output_dir}/")


if __name__ == "__main__":
    main()
