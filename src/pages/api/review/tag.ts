import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken, invalidateItemDepartments } from "../../../lib/supabase";
import { loadDepartments } from "../../../lib/data-loader";

export const prerender = false;

const VALID_CORPS = new Set(["south", "central", "east", "west", "north"]);

function getToken(request: Request): string | null {
  const header = request.headers.get("x-edit-token");
  if (header) return header;
  const cookie = request.headers.get("cookie") ?? "";
  const match = cookie.match(/(?:^|;\s*)review_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export const POST: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: { corpId?: string; itemId?: string; departments?: string[] };
  try {
    body = await request.json();
  } catch {
    return new Response("Bad JSON", { status: 400 });
  }
  const { corpId, itemId, departments } = body;
  if (!corpId || !VALID_CORPS.has(corpId)) return new Response("Bad corpId", { status: 400 });
  if (!itemId || typeof itemId !== "string") return new Response("Bad itemId", { status: 400 });
  if (!Array.isArray(departments)) return new Response("Bad departments", { status: 400 });

  const validDeptIds = new Set(loadDepartments("bengaluru").departments.map((d) => d.id));
  const cleaned = Array.from(
    new Set(departments.filter((d) => typeof d === "string" && validDeptIds.has(d)))
  );

  const { data: prior } = await supabaseAdmin
    .from("city_item_departments")
    .select("departments")
    .eq("corp_id", corpId)
    .eq("item_id", itemId)
    .maybeSingle();

  const before = (prior?.departments as string[] | undefined) ?? null;

  const { error: upsertErr } = await supabaseAdmin
    .from("city_item_departments")
    .upsert(
      {
        corp_id: corpId,
        item_id: itemId,
        departments: cleaned,
        editor_name: editor,
        updated_at: new Date().toISOString(),
      },
      { onConflict: "corp_id,item_id" }
    );
  if (upsertErr) return new Response(upsertErr.message, { status: 500 });

  await supabaseAdmin.from("city_item_departments_history").insert({
    corp_id: corpId,
    item_id: itemId,
    before,
    after: cleaned,
    editor_name: editor,
  });

  invalidateItemDepartments(corpId);
  return new Response(JSON.stringify({ ok: true, departments: cleaned, editor }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};
