export interface City {
  id: string;
  name: string;
  name_local: string;
  state: string;
  act: { name: string; short_name: string; year: number };
  authority: string;
  corporation_count: number;
  budget_year: string;
}

export interface Corporation {
  id: string;
  name: string;
  name_local: string;
  wards: number;
  budget_year: string;
}

export interface FunctionMapping {
  id: string;
  name: string;
  name_local: string;
  category: "core" | "general" | "sector";
  act_reference: { section: string; schedule: string; text: string };
  devolution: "obligatory" | "discretionary" | "not_devolved";
  budget_heads: string[];
  notf_cms_departments: string[];
  notf_cms_categories: string[];
}

export interface BudgetData {
  corporation_id: string;
  fiscal_year: string;
  summary: { total_revenue: number; total_expenditure: number; unit: string };
  by_function: Record<string, { amount: number; share: number; yoy_growth: number }>;
  staff_cost?: Record<string, { amount: number; pct_of_dept_budget: number }>;
  by_department?: Record<string, number>;
  by_department_aggregated?: Record<string, {
    amount: number;
    share: number;
    staff_cost: { amount: number; pct_of_dept_budget: number };
  }>;
}

export interface DepartmentFunction {
  id: string;
  scope: string;
}

export interface Department {
  id: string;
  name: string;
  name_local: string;
  description: string;
  budget_heads: string[];
  budget_note?: string;
  functions: DepartmentFunction[];
  grievance_categories: string[];
}

export interface ExternalEntity {
  id: string;
  name: string;
  abbr: string;
  type: "parastatal" | "state_dept" | "central_body" | "private";
  url: string;
  functions: DepartmentFunction[];
}

export interface DepartmentsData {
  departments: Department[];
  external_entities: ExternalEntity[];
}

export interface GrievanceData {
  corporation_id: string;
  source: string;
  total_complaints: number;
  closed: number;
  open: number;
  resolution_rate: number;
  avg_response_time: string;
  by_function: Record<string, { count: number; share: number; resolution_rate: number }>;
  by_department?: Record<string, { count: number; share: number; resolution_rate: number }>;
  top_wards: string[];
}

export interface DepartmentHealth {
  department_id: string;
  corporation_id: string;
  budget_amount: number;
  budget_share: number;
  staff_amount: number;
  staff_pct_of_budget: number;
  complaint_count: number;
  complaint_share: number;
  resolution_rate: number;
  stress_index: number;
  status: HealthStatus;
}

export interface ActSection {
  number: string;
  title: string;
}

export interface ActChapter {
  number: string;
  title: string;
  page_start: number;
  page_end: number;
  sections: ActSection[];
}

export interface ActScheduleFunction {
  number: string;
  text: string;
}

export interface ActSectorFunction {
  number: string;
  title: string;
  items: { sub: string; text: string }[];
}

export interface ActScheduleI {
  title: string;
  section_reference: string;
  core_functions: ActScheduleFunction[];
  general_functions: ActScheduleFunction[];
  sector_functions: ActSectorFunction[];
}

export interface ActData {
  title: string;
  act_number: string;
  pages: number;
  source: string;
  chapters: ActChapter[];
  schedules: { I: ActScheduleI };
}

export type HealthStatus = "good" | "stressed" | "critical" | "unfunded" | "no_data";

export interface FunctionHealth {
  function_id: string;
  corporation_id: string;
  budget_amount: number;
  budget_share: number;
  complaint_count: number;
  complaint_share: number;
  resolution_rate: number;
  stress_index: number;
  status: HealthStatus;
}

export interface LineItem {
  code: string;
  name: string;
  revised_2025_26: number | null;
  pending: number | null;
  current: number | null;
  total_2026_27: number;
  yoy_pct: number | null;
}

export interface LineItemSubCategory {
  name: string;
  total_2026_27: number;
  revised_2025_26: number;
  spillover: number;
  yoy_pct: number | null;
  items: LineItem[];
}

export interface LineItemFunction {
  code: string;
  name: string;
  total_2026_27: number;
  revised_2025_26: number;
  spillover: number;
  yoy_pct: number | null;
  sub_categories: LineItemSubCategory[];
}

export interface LineItemBudget {
  corporation_id: string;
  fiscal_year: string;
  unit: string;
  total_2026_27: number;
  total_revised_2025_26: number;
  total_spillover: number;
  yoy_pct: number | null;
  functions: LineItemFunction[];
}
