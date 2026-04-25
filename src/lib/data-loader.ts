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

// A "category" is a thematic section from the Commissioner's speech (e.g. welfare,
// public-works). Each corp has its own set — read from the speech JSON's sections[].
export interface CategoryDef {
  code: string;
  title: string;
  title_local: string | null;
  page_start?: number;
  page_end?: number;
}

// Display-title overrides. Codes stay stable (DB rows, CSS palette), only labels change.
const TITLE_OVERRIDES: Record<string, string> = {
  "animal-husbandry": "Animal Welfare",
  sanitation: "Waste Management",
  "land-acquisition": "Land, DRC and TDR",
};

function applyTitleOverride(code: string, title: string): string {
  return TITLE_OVERRIDES[code] ?? title;
}

// Synchronous, speech-only — used in API handlers for validation.
export function loadCategories(cityId: string, corpId: string, year = "2026-27"): CategoryDef[] {
  const speech = loadSpeechProjects(cityId, corpId, year);
  const raw = (speech as any)?.sections ?? [];
  return raw.map((s: any) => ({
    code: s.code,
    title: applyTitleOverride(s.code, s.title),
    title_local: s.title_local ?? null,
    page_start: s.page_start,
    page_end: s.page_end,
  }));
}

// Async, merges in editor-added custom categories. Returns natural order:
// speech sections first (extraction order), then custom (insertion order).
// Callers apply their own sort using loadCategoryOrder + a fallback rule.
export async function loadAllCategories(
  cityId: string,
  corpId: string,
  year = "2026-27",
): Promise<CategoryDef[]> {
  const { loadCustomCategories } = await import("./supabase");
  const custom = await loadCustomCategories(corpId);
  const speech = loadCategories(cityId, corpId, year);
  const customDefs: CategoryDef[] = custom.map((c) => ({
    code: c.code,
    title: c.title,
    title_local: c.title_local,
  }));
  return [...speech, ...customDefs];
}

export interface MergedItem {
  id: string;
  name: string;
  name_local: string | null;
  description: string;
  amount_lakhs: number | null;
  page: number;
  verbatim_quote: string;
  section: string | null;              // from extraction (auto-tag)
  section_local: string | null;
  effective_category: string;           // override if present, else section, else 'uncategorised'
  has_override: boolean;
  source: "speech" | "manual";
}

export async function loadMergedItems(
  cityId: string,
  corpId: string,
  year = "2026-27"
): Promise<MergedItem[]> {
  const { loadItemCategories, loadManualItems } = await import("./supabase");
  const base = loadSpeechProjects(cityId, corpId, year);
  const [overrides, manuals] = await Promise.all([
    loadItemCategories(corpId),
    loadManualItems(corpId),
  ]);

  const out: MergedItem[] = [];
  for (const p of base?.projects ?? []) {
    const override = overrides.get(p.id);
    const section = (p as any).section ?? null;
    out.push({
      id: p.id,
      name: p.name,
      name_local: p.name_local ?? null,
      description: p.description ?? "",
      amount_lakhs: p.amount_lakhs,
      page: p.page,
      verbatim_quote: p.verbatim_quote ?? "",
      section,
      section_local: (p as any).section_local ?? null,
      effective_category: override ?? section ?? "uncategorised",
      has_override: !!override,
      source: "speech",
    });
  }
  for (const m of manuals) {
    const override = overrides.get(m.item_id) ?? m.category_override ?? null;
    const section = m.section ?? "manual";
    out.push({
      id: m.item_id,
      name: m.name,
      name_local: null,
      description: m.description ?? "",
      amount_lakhs: m.amount_lakhs,
      page: m.page ?? 0,
      verbatim_quote: m.verbatim_quote ?? "",
      section,
      section_local: null,
      effective_category: override ?? section,
      has_override: !!override,
      source: "manual",
    });
  }
  return out;
}
