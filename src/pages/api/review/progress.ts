import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken } from "../../../lib/supabase";

export const prerender = false;

const VALID_CORPS = new Set(["south", "central", "east", "west", "north"]);
const VALID_STATUSES = new Set(["began", "wip", "done"]);

function getToken(request: Request): string | null {
  return request.headers.get("x-edit-token");
}

// POST { corpId, itemId, status: 'began'|'wip'|'done'|null }
// status=null clears the row.
export const POST: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: any;
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }

  const { corpId, itemId, status } = body ?? {};
  if (!corpId || !VALID_CORPS.has(corpId)) return new Response("Bad corpId", { status: 400 });
  if (!itemId || typeof itemId !== "string") return new Response("Bad itemId", { status: 400 });

  if (status === null) {
    const { error } = await supabaseAdmin
      .from("city_item_progress")
      .delete()
      .eq("corp_id", corpId)
      .eq("item_id", itemId);
    if (error) return new Response(error.message, { status: 500 });
    return new Response(JSON.stringify({ ok: true, status: null }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }

  if (typeof status !== "string" || !VALID_STATUSES.has(status)) {
    return new Response("status must be 'began' | 'wip' | 'done' | null", { status: 400 });
  }

  const { error } = await supabaseAdmin
    .from("city_item_progress")
    .upsert(
      {
        corp_id: corpId,
        item_id: itemId,
        status,
        editor_name: editor,
        updated_at: new Date().toISOString(),
      },
      { onConflict: "corp_id,item_id" }
    );
  if (error) return new Response(error.message, { status: 500 });

  return new Response(JSON.stringify({ ok: true, status }), {
    status: 200, headers: { "content-type": "application/json" },
  });
};
