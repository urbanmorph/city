import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken, invalidateItemCategories } from "../../../lib/supabase";
import { loadCategories } from "../../../lib/data-loader";

export const prerender = false;

const VALID_CORPS = new Set(["south", "central", "east", "west", "north"]);

function getToken(request: Request): string | null {
  const header = request.headers.get("x-edit-token");
  if (header) return header;
  const cookie = request.headers.get("cookie") ?? "";
  const match = cookie.match(/(?:^|;\s*)review_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9\s-]/g, "").trim().replace(/\s+/g, "-").slice(0, 48);
}

export const POST: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: any;
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }

  const { corpId, name, description, amount_lakhs, page, verbatim_quote, category } = body ?? {};
  if (!corpId || !VALID_CORPS.has(corpId)) return new Response("Bad corpId", { status: 400 });
  if (!name || typeof name !== "string" || name.trim().length < 3) return new Response("Name required (≥3 chars)", { status: 400 });
  if (!category || typeof category !== "string") return new Response("Category required", { status: 400 });

  const validCats = new Set(loadCategories("bengaluru", corpId).map((c) => c.code));
  if (!validCats.has(category)) return new Response("Unknown category for this corp", { status: 400 });

  const item_id = `manual-${slugify(name)}-${Date.now().toString(36)}`;
  const row = {
    corp_id: corpId,
    item_id,
    name: name.trim(),
    description: typeof description === "string" ? description.trim() : "",
    amount_lakhs: typeof amount_lakhs === "number" && isFinite(amount_lakhs) ? amount_lakhs : null,
    page: typeof page === "number" && isFinite(page) ? Math.round(page) : null,
    verbatim_quote: typeof verbatim_quote === "string" ? verbatim_quote.trim() : "",
    section: category,
    category_override: null,
    departments: [],
    editor_name: editor,
    updated_at: new Date().toISOString(),
  };

  const { error } = await supabaseAdmin.from("city_manual_items").insert(row);
  if (error) return new Response(error.message, { status: 500 });

  invalidateItemCategories(corpId);
  return new Response(JSON.stringify({ ok: true, item: row }), {
    status: 200, headers: { "content-type": "application/json" },
  });
};

export const DELETE: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: any;
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }
  const { corpId, itemId } = body ?? {};
  if (!corpId || !VALID_CORPS.has(corpId) || !itemId) return new Response("Bad input", { status: 400 });

  const { error } = await supabaseAdmin
    .from("city_manual_items")
    .delete()
    .eq("corp_id", corpId)
    .eq("item_id", itemId);
  if (error) return new Response(error.message, { status: 500 });

  invalidateItemCategories(corpId);
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
};
