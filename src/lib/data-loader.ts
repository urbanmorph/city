import fs from "node:fs";
import path from "node:path";
import type { City, Corporation, FunctionMapping, BudgetData, GrievanceData, ActData, DepartmentsData, LineItemBudget, SpeechProjects } from "./types";

const dataDir = path.join(process.cwd(), "data");

function readJSON<T>(filePath: string): T | null {
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function loadCities(): City[] {
  return readJSON<City[]>(path.join(dataDir, "cities.json")) ?? [];
}

export function loadCity(cityId: string): City | null {
  const cities = loadCities();
  return cities.find((c) => c.id === cityId) ?? null;
}

export function loadCorporations(cityId: string): Corporation[] {
  return readJSON<Corporation[]>(path.join(dataDir, cityId, "corporations.json")) ?? [];
}

export function loadCorporation(cityId: string, corpId: string): Corporation | null {
  const corps = loadCorporations(cityId);
  return corps.find((c) => c.id === corpId) ?? null;
}

export function loadFunctionMapping(cityId: string): FunctionMapping[] {
  const data = readJSON<{ functions: FunctionMapping[] }>(
    path.join(dataDir, cityId, "function-mapping.json")
  );
  return data?.functions ?? [];
}

export function loadBudget(cityId: string, corpId: string, year = "2026-27"): BudgetData | null {
  return readJSON<BudgetData>(path.join(dataDir, cityId, "budgets", year, `${corpId}.json`));
}

export function loadGrievances(cityId: string, corpId: string): GrievanceData | null {
  return readJSON<GrievanceData>(path.join(dataDir, cityId, "grievances", `${corpId}.json`));
}

export function loadAct(cityId: string): ActData | null {
  return readJSON<ActData>(path.join(dataDir, cityId, "act", "gba-act.json"));
}

export function loadDepartments(cityId: string): DepartmentsData {
  const data = readJSON<DepartmentsData>(path.join(dataDir, cityId, "departments.json"));
  return data ?? { departments: [], external_entities: [] };
}

export function loadLineItemBudget(cityId: string, corpId: string, year = "2026-27"): LineItemBudget | null {
  return readJSON<LineItemBudget>(
    path.join(dataDir, cityId, "budgets", year, "lineitems", `${corpId}.json`)
  );
}

export function loadSpeechProjects(cityId: string, corpId: string, year = "2026-27"): SpeechProjects | null {
  return readJSON<SpeechProjects>(
    path.join(dataDir, cityId, "budgets", year, "speech", `${corpId}-projects.tagged.json`)
  );
}

// Department → Schedule-I function the department rolls up to.
// Matches departments.json budget_heads (and budget_notes for "funded under …").
export const DEPT_TO_FUNCTION: Record<string, string> = {
  engineering: "05",
  revenue: "03",
  advertisement: "03",
  "health-sanitation": "07",
  "animal-husbandry": "07",
  "forest-horticulture": "09",
  administration: "02",
  finance: "02",
  "public-relations": "02",
  "solid-waste": "06",
  welfare: "12",
  "town-planning": "04",
  "land-acquisition": "04",
  education: "11",
};

export interface MergedProject {
  id: string;
  name: string;
  name_local: string | null;
  description: string;
  amount_lakhs: number | null;
  page: number;
  category: string;
  verbatim_quote: string;
  function_code: string | null;
  function_name: string | null;
  tag_reason: string;
  section?: string | null;
  section_local?: string | null;
  departments: string[];      // editor-assigned; first = primary, rest = secondary
  primary_department: string | null;
  secondary_departments: string[];
  effective_function_code: string | null; // primary dept's function if tagged, else heuristic function_code
  source: "speech" | "manual";
}

export async function loadMergedSpeechProjects(
  cityId: string,
  corpId: string,
  year = "2026-27"
): Promise<{ base: SpeechProjects | null; merged: MergedProject[] }> {
  const { loadItemDepartments, loadManualItems } = await import("./supabase");
  const base = loadSpeechProjects(cityId, corpId, year);
  const [overrides, manuals] = await Promise.all([
    loadItemDepartments(corpId),
    loadManualItems(corpId),
  ]);

  const merged: MergedProject[] = [];
  for (const p of base?.projects ?? []) {
    const depts = overrides.get(p.id) ?? [];
    const primary = depts[0] ?? null;
    merged.push({
      ...(p as any),
      section: (p as any).section ?? null,
      section_local: (p as any).section_local ?? null,
      departments: depts,
      primary_department: primary,
      secondary_departments: depts.slice(1),
      effective_function_code: primary ? (DEPT_TO_FUNCTION[primary] ?? p.function_code) : p.function_code,
      source: "speech",
    });
  }
  for (const m of manuals) {
    const depts = m.departments ?? [];
    const primary = depts[0] ?? null;
    merged.push({
      id: m.item_id,
      name: m.name,
      name_local: null,
      description: m.description ?? "",
      amount_lakhs: m.amount_lakhs,
      page: m.page ?? 0,
      category: "manual",
      verbatim_quote: m.verbatim_quote ?? "",
      function_code: null,
      function_name: null,
      tag_reason: "manually added",
      section: m.section ?? "manual",
      section_local: null,
      departments: depts,
      primary_department: primary,
      secondary_departments: depts.slice(1),
      effective_function_code: primary ? (DEPT_TO_FUNCTION[primary] ?? null) : null,
      source: "manual",
    });
  }
  return { base, merged };
}
