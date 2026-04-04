/** Convert lakhs to human-readable INR string. */
export function formatINR(lakhs: number): string {
  if (lakhs >= 100) {
    const crores = lakhs / 100;
    return `\u20B9${crores.toFixed(2)} Cr`;
  }
  return `\u20B9${lakhs} L`;
}

/** Convert a decimal ratio to percentage string: 0.052 -> "5.2%" */
export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** Format a number with en-IN locale grouping: 39913 -> "39,913" */
export function formatNumber(value: number): string {
  return value.toLocaleString("en-IN");
}

/** Format a growth ratio with sign: 0.19 -> "+19%", -0.05 -> "-5%" */
export function formatGrowth(value: number): string {
  const pct = Math.round(value * 100);
  return pct >= 0 ? `+${pct}%` : `${pct}%`;
}
