import type { APIRoute } from "astro";
import { editorForToken } from "../../../lib/supabase";

export const prerender = false;

function getToken(request: Request): string | null {
  return request.headers.get("x-edit-token");
}

// Server-side proxy to Google Translate's free unofficial endpoint.
// Avoids CORS, hides the raw URL pattern, and lets us swap providers later
// without touching the client.
export const POST: APIRoute = async ({ request }) => {
  if (!editorForToken(getToken(request))) return new Response("Forbidden", { status: 403 });

  let body: { text?: string; target?: string };
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }

  const text = (body.text ?? "").trim();
  const target = (body.target ?? "kn").trim();
  if (!text || text.length > 200) return new Response("text required (≤ 200 chars)", { status: 400 });

  const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=${encodeURIComponent(target)}&dt=t&q=${encodeURIComponent(text)}`;
  try {
    const res = await fetch(url, { headers: { "user-agent": "Mozilla/5.0 city-latte/1.0" } });
    if (!res.ok) return new Response(`upstream ${res.status}`, { status: 502 });
    const json = (await res.json()) as unknown;
    // Shape: [[["ಚಲನಶೀಲತೆ","Mobility",null,null,1]], null, "en"]
    let translated = "";
    if (Array.isArray(json) && Array.isArray(json[0])) {
      for (const seg of json[0] as unknown[]) {
        if (Array.isArray(seg) && typeof seg[0] === "string") translated += seg[0];
      }
    }
    return new Response(JSON.stringify({ translated }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return new Response(`translate failed: ${(err as Error).message}`, { status: 502 });
  }
};
