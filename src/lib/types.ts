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
  top_wards: string[];
}

export type HealthStatus = "good" | "stressed" | "critical" | "unfunded";

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
