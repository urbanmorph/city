import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken, invalidateItemCategories } from "../../../lib/supabase";
import { loadAllCategories } from "../../../lib/data-loader";

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
  return s
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 48);
}

export const POST: APIRoute = async ({ request }) => {
  const editor = editorForToken(getToken(request));
  if (!editor) return new Response("Forbidden", { status: 403 });
  if (!supabaseAdmin) return new Response("Service not configured", { status: 500 });

  let body: any;
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }

  const { corpId, title, title_local } = body ?? {};
  if (!corpId || !VALID_CORPS.has(corpId)) return new Response("Bad corpId", { status: 400 });
  if (!title || typeof title !== "string" || title.trim().length < 2) {
    return new Response("Title required (≥2 chars)", { status: 400 });
  }
  const cleanTitle = title.trim();
  const code = slugify(cleanTitle);
  if (!code) return new Response("Title produces empty code", { status: 400 });

  const existing = await loadAllCategories("bengaluru", corpId);
  if (existing.some((c) => c.code === code)) {
    return new Response("Category code already exists", { status: 409 });
  }

  const { error } = await supabaseAdmin.from("city_custom_categories").insert({
    corp_id: corpId,
    code,
    title: cleanTitle,
    title_local: typeof title_local === "string" && title_local.trim() ? title_local.trim() : null,
    editor_name: editor,
  });
  if (error) return new Response(error.message, { status: 500 });

  invalidateItemCategories(corpId);
  return new Response(JSON.stringify({ ok: true, code, title: cleanTitle }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};
