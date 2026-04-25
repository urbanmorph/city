import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken } from "../../../lib/supabase";

export const prerender = false;

const VALID_CORPS = new Set(["south", "central", "east", "west", "north"]);

function getToken(request: Request): string | null {
  const header = request.headers.get("x-edit-token");
  if (header) return header;
  const cookie = request.headers.get("cookie") ?? "";
  const match = cookie.match(/(?:^|;\s*)review_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

// POST { corpId, order: ["welfare", "public-works", ...] }
// Order array is the full ordering — index in array becomes sort_order.
export const POST: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: any;
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }

  const { corpId, order } = body ?? {};
  if (!corpId || !VALID_CORPS.has(corpId)) return new Response("Bad corpId", { status: 400 });
  if (!Array.isArray(order)) return new Response("order must be an array of codes", { status: 400 });

  const rows = order
    .filter((c) => typeof c === "string" && c.length > 0)
    .map((code: string, idx: number) => ({
      corp_id: corpId,
      code,
      sort_order: idx,
      editor_name: editor,
      updated_at: new Date().toISOString(),
    }));
  if (rows.length === 0) return new Response("order is empty", { status: 400 });

  const { error } = await supabaseAdmin
    .from("city_category_order")
    .upsert(rows, { onConflict: "corp_id,code" });
  if (error) return new Response(error.message, { status: 500 });

  return new Response(JSON.stringify({ ok: true, count: rows.length }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};
