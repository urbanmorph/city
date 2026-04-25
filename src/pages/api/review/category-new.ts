import type { APIRoute } from "astro";
import { supabaseAdmin, editorForToken, invalidateItemCategories } from "../../../lib/supabase";
import { loadAllCategories } from "../../../lib/data-loader";

export const prerender = false;

const VALID_CORPS = new Set(["south", "central", "east", "west", "north"]);

function getToken(request: Request): string | null {
  return request.headers.get("x-edit-token");
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 48);
}

async function translateToKannada(text: string): Promise<string | null> {
  try {
    const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=kn&dt=t&q=${encodeURIComponent(text)}`;
    const res = await fetch(url, { headers: { "user-agent": "Mozilla/5.0 city-latte/1.0" } });
    if (!res.ok) return null;
    const json = (await res.json()) as unknown;
    let translated = "";
    if (Array.isArray(json) && Array.isArray(json[0])) {
      for (const seg of json[0] as unknown[]) {
        if (Array.isArray(seg) && typeof seg[0] === "string") translated += seg[0];
      }
    }
    return translated.trim() || null;
  } catch {
    return null;
  }
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

  // Resolve Kannada title: editor's input wins; otherwise translate as a server-side
  // fallback so we don't lose to client-side debounce races. If translation fails,
  // store null and the editor can fill it in later.
  let resolvedLocal: string | null = null;
  if (typeof title_local === "string" && title_local.trim()) {
    resolvedLocal = title_local.trim();
  } else {
    resolvedLocal = await translateToKannada(cleanTitle);
  }

  const { error } = await supabaseAdmin.from("city_custom_categories").insert({
    corp_id: corpId,
    code,
    title: cleanTitle,
    title_local: resolvedLocal,
    editor_name: editor,
  });
  if (error) return new Response(error.message, { status: 500 });

  invalidateItemCategories(corpId);
  return new Response(JSON.stringify({ ok: true, code, title: cleanTitle }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};
