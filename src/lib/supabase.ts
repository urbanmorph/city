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

// No in-memory cache for now — reviewers iterate fast and want immediate feedback.
// Supabase anon reads are sub-100ms; re-enable a short TTL if traffic grows.

export async function loadItemCategories(corpId: string): Promise<Map<string, string>> {
  if (!supabaseAnon) return new Map();
  const { data, error } = await supabaseAnon
    .from("city_item_categories")
    .select("item_id, category")
    .eq("corp_id", corpId);
  if (error || !data) return new Map();
  const rows = new Map<string, string>();
  for (const r of data as { item_id: string; category: string }[]) {
    rows.set(r.item_id, r.category);
  }
  return rows;
}

export function invalidateItemCategories(_corpId: string) {
  // no-op while cache is disabled
}

export interface ManualItem {
  item_id: string;
  name: string;
  description: string | null;
  amount_lakhs: number | null;
  page: number | null;
  verbatim_quote: string | null;
  section: string | null;
  category_override: string | null;
  editor_name: string | null;
  created_at: string;
  updated_at: string;
}

export async function loadManualItems(corpId: string): Promise<ManualItem[]> {
  if (!supabaseAnon) return [];
  const { data, error } = await supabaseAnon
    .from("city_manual_items")
    .select("item_id,name,description,amount_lakhs,page,verbatim_quote,section,category_override,editor_name,created_at,updated_at")
    .eq("corp_id", corpId)
    .order("created_at", { ascending: true });
  if (error || !data) return [];
  return data as ManualItem[];
}
