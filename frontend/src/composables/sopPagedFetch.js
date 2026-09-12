// Shared paginator for S&OP tables that can exceed PostgREST's default/max
// 1000-row-per-request cap (sop_daily_sales, sop_production_daily,
// production_plan_snapshots all grow past that within a couple months) --
// a plain .select("*") on any of these would silently truncate instead of
// erroring, so every S&OP composable that queries one of them must fetch
// in pages until a page comes back short, not just once.
import { supabase } from "../supabaseClient.js";

const PAGE_SIZE = 1000;

export async function fetchAllRows(table, applyFilters) {
  const all = [];
  let from = 0;
  for (;;) {
    let query = supabase.from(table).select("*").range(from, from + PAGE_SIZE - 1);
    if (applyFilters) query = applyFilters(query);
    const { data, error } = await query;
    if (error) return { data: null, error };
    all.push(...data);
    if (data.length < PAGE_SIZE) break;
    from += PAGE_SIZE;
  }
  return { data: all, error: null };
}
