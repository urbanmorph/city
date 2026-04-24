import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.PUBLIC_SUPABASE_URL;
const key = import.meta.env.PUBLIC_SUPABASE_ANON_KEY;

export function subscribeCorpRealtime(corpId: string | null): () => void {
  if (!corpId || !url || !key) return () => {};
  const supa = createClient(url, key, { auth: { persistSession: false } });

  let debounce: ReturnType<typeof setTimeout> | null = null;
  const onChange = () => {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => location.reload(), 800);
  };

  const channel = supa
    .channel(`corp-${corpId}`)
    .on(
      "postgres_changes",
      { event: "*", schema: "public", table: "city_item_categories", filter: `corp_id=eq.${corpId}` },
      onChange,
    )
    .on(
      "postgres_changes",
      { event: "*", schema: "public", table: "city_manual_items", filter: `corp_id=eq.${corpId}` },
      onChange,
    )
    .subscribe();

  return () => {
    if (debounce) clearTimeout(debounce);
    supa.removeChannel(channel);
  };
}
