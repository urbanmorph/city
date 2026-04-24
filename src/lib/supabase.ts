import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.PUBLIC_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.PUBLIC_SUPABASE_ANON_KEY as string | undefined;
const serviceKey = import.meta.env.SUPABASE_SERVICE_ROLE_KEY as string | undefined;

export const supabaseAnon =
  url && anonKey ? createClient(url, anonKey, { auth: { persistSession: false } }) : null;

export const supabaseAdmin =
  url && serviceKey ? createClient(url, serviceKey, { auth: { persistSession: false } }) : null;

export function parseEditTokens(raw: string | undefined): Map<string, string> {
  const map = new Map<string, string>();
  if (!raw) return map;
  for (const pair of raw.split(",")) {
    const [name, token] = pair.split(":").map((s) => s?.trim());
    if (name && token) map.set(token, name);
  }
  return map;
}

export function editorForToken(token: string | null | undefined): string | null {
  if (!token) return null;
  const tokens = parseEditTokens(import.meta.env.EDIT_TOKENS as string | undefined);
  return tokens.get(token) ?? null;
}

export interface ItemDepartmentRow {
  corp_id: string;
  item_id: string;
  departments: string[];
  editor_name: string | null;
  updated_at: string;
}

let cache: { key: string; at: number; rows: Map<string, string[]> } | null = null;
const TTL_MS = 60_000;

export async function loadItemDepartments(corpId: string): Promise<Map<string, string[]>> {
  const now = Date.now();
  if (cache && cache.key === corpId && now - cache.at < TTL_MS) return cache.rows;
  if (!supabaseAnon) return new Map();
  const { data, error } = await supabaseAnon
    .from("city_item_departments")
    .select("item_id, departments")
    .eq("corp_id", corpId);
  if (error || !data) return cache?.rows ?? new Map();
  const rows = new Map<string, string[]>();
  for (const r of data as { item_id: string; departments: string[] }[]) {
    rows.set(r.item_id, r.departments ?? []);
  }
  cache = { key: corpId, at: now, rows };
  return rows;
}

export function invalidateItemDepartments(corpId: string) {
  if (cache && cache.key === corpId) cache = null;
  if (manualCache && manualCache.key === corpId) manualCache = null;
}

export interface ManualItem {
  item_id: string;
  name: string;
  description: string | null;
  amount_lakhs: number | null;
  page: number | null;
  verbatim_quote: string | null;
  section: string | null;
  departments: string[];
  editor_name: string | null;
  created_at: string;
  updated_at: string;
}

let manualCache: { key: string; at: number; rows: ManualItem[] } | null = null;

export async function loadManualItems(corpId: string): Promise<ManualItem[]> {
  const now = Date.now();
  if (manualCache && manualCache.key === corpId && now - manualCache.at < TTL_MS) return manualCache.rows;
  if (!supabaseAnon) return [];
  const { data, error } = await supabaseAnon
    .from("city_manual_items")
    .select("item_id,name,description,amount_lakhs,page,verbatim_quote,section,departments,editor_name,created_at,updated_at")
    .eq("corp_id", corpId)
    .order("created_at", { ascending: true });
  if (error || !data) return manualCache?.rows ?? [];
  const rows = data as ManualItem[];
  manualCache = { key: corpId, at: now, rows };
  return rows;
}
