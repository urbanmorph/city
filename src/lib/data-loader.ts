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
