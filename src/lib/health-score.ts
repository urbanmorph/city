import type { BudgetData, GrievanceData, FunctionMapping, FunctionHealth, HealthStatus } from "./types";

export function computeFunctionHealth(
  functionId: string,
  corpId: string,
  budget: BudgetData,
  grievances: GrievanceData,
  mapping: FunctionMapping[]
): FunctionHealth {
  const fn = mapping.find((m) => m.id === functionId);
  const budgetEntry = budget.by_function[functionId];
  const grievanceEntry = grievances.by_function[functionId];

  const budgetAmount = budgetEntry?.amount ?? 0;
  const budgetShare = budgetEntry?.share ?? 0;
  const complaintCount = grievanceEntry?.count ?? 0;
  const complaintShare = grievanceEntry?.share ?? 0;
  const resolutionRate = grievanceEntry?.resolution_rate ?? 0;

  const stressIndex = budgetShare > 0 ? complaintShare / budgetShare : 0;

  const isActMandated = fn?.devolution === "obligatory";
  const hasComplaintData = complaintCount > 0;
  // Use corp-level resolution rate as fallback when no per-function data exists
  const effectiveResolution = hasComplaintData ? resolutionRate : grievances.resolution_rate;

  let status: HealthStatus;
  if (budgetShare < 0.01 && isActMandated) {
    status = "unfunded";
  } else if (stressIndex < 2.0 && effectiveResolution > 80) {
    status = "good";
  } else if (stressIndex > 5.0 || (hasComplaintData && effectiveResolution < 60)) {
    status = "critical";
  } else {
    status = "stressed";
  }

  return {
    function_id: functionId,
    corporation_id: corpId,
    budget_amount: budgetAmount,
    budget_share: budgetShare,
    complaint_count: complaintCount,
    complaint_share: complaintShare,
    resolution_rate: resolutionRate,
    stress_index: stressIndex,
    status,
  };
}

export function computeAllHealth(
  corpId: string,
  budget: BudgetData,
  grievances: GrievanceData,
  mapping: FunctionMapping[]
): FunctionHealth[] {
  return mapping.map((fn) =>
    computeFunctionHealth(fn.id, corpId, budget, grievances, mapping)
  );
}

export function countByStatus(healthList: FunctionHealth[]): Record<HealthStatus, number> {
  const counts: Record<HealthStatus, number> = {
    good: 0,
    stressed: 0,
    critical: 0,
    unfunded: 0,
  };
  for (const h of healthList) {
    counts[h.status]++;
  }
  return counts;
}
