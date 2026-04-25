import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken, invalidateItemCategories } from "../../../lib/supabase";
import { loadAllCategories } from "../../../lib/data-loader";

export const prerender = false;

const VALID_CORPS = new Set(["south", "central", "east", "west", "north"]);

function getToken(request: Request): string | null {
  return request.headers.get("x-edit-token");
}

export const POST: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: { corpId?: string; itemId?: string; category?: string };
  try {
    body = await request.json();
  } catch {
    return new Response("Bad JSON", { status: 400 });
  }
  const { corpId, itemId, category } = body;
  if (!corpId || !VALID_CORPS.has(corpId)) return new Response("Bad corpId", { status: 400 });
  if (!itemId || typeof itemId !== "string") return new Response("Bad itemId", { status: 400 });
  if (!category || typeof category !== "string") return new Response("Bad category", { status: 400 });

  const allCats = await loadAllCategories("bengaluru", corpId);
  const validCats = new Set(allCats.map((c) => c.code));
  if (!validCats.has(category)) return new Response("Unknown category for this corp", { status: 400 });

  const { data: prior } = await supabaseAdmin
    .from("city_item_categories")
    .select("category")
    .eq("corp_id", corpId)
    .eq("item_id", itemId)
    .maybeSingle();

  const before = (prior?.category as string | undefined) ?? null;

  const { error: upsertErr } = await supabaseAdmin
    .from("city_item_categories")
    .upsert(
      {
        corp_id: corpId,
        item_id: itemId,
        category,
        editor_name: editor,
        updated_at: new Date().toISOString(),
      },
      { onConflict: "corp_id,item_id" }
    );
  if (upsertErr) return new Response(upsertErr.message, { status: 500 });

  await supabaseAdmin.from("city_item_categories_history").insert({
    corp_id: corpId,
    item_id: itemId,
    before,
    after: category,
    editor_name: editor,
  });

  // If the item left a custom category empty, garbage-collect it.
  let deleted_category: string | null = null;
  if (before && before !== category) {
    const removed = await maybeDeleteEmptyCustomCategory(corpId, before);
    if (removed) deleted_category = before;
  }

  invalidateItemCategories(corpId);
  return new Response(JSON.stringify({ ok: true, category, editor, deleted_category }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};

// If `code` refers to a custom category and no item references it any more,
// delete the custom row + its order row. Speech sections are never deleted.
async function maybeDeleteEmptyCustomCategory(corpId: string, code: string): Promise<boolean> {
  if (!supabaseAdmin) return false;

  const { data: customRow } = await supabaseAdmin
    .from("city_custom_categories")
    .select("code")
    .eq("corp_id", corpId)
    .eq("code", code)
    .maybeSingle();
  if (!customRow) return false;

  const { count: overrideCount } = await supabaseAdmin
    .from("city_item_categories")
    .select("item_id", { count: "exact", head: true })
    .eq("corp_id", corpId)
    .eq("category", code);
  if ((overrideCount ?? 0) > 0) return false;

  const { count: manualSectionCount } = await supabaseAdmin
    .from("city_manual_items")
    .select("item_id", { count: "exact", head: true })
    .eq("corp_id", corpId)
    .eq("section", code);
  if ((manualSectionCount ?? 0) > 0) return false;

  const { count: manualOverrideCount } = await supabaseAdmin
    .from("city_manual_items")
    .select("item_id", { count: "exact", head: true })
    .eq("corp_id", corpId)
    .eq("category_override", code);
  if ((manualOverrideCount ?? 0) > 0) return false;

  await supabaseAdmin.from("city_custom_categories").delete().eq("corp_id", corpId).eq("code", code);
  await supabaseAdmin.from("city_category_order").delete().eq("corp_id", corpId).eq("code", code);
  return true;
}
