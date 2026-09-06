// Shared Supabase client for every page in this project.
//
// Both values below are the PUBLIC project URL and the `anon`/publishable
// key -- meant to be shipped in client-side code, safe to commit. They
// grant no access on their own; every table has Row Level Security
// enabled (see supabase/schema.sql), so what a logged-in user can
// actually read is enforced by Postgres, not by this file.
//
// Never put the service_role key here -- it bypasses RLS entirely and
// must only ever exist as a GitHub Actions secret and a Supabase Edge
// Function secret.

import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = "https://jfxfzulufaxrmopnvpqa.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_LNoy7fE1VMV1V7ygcUhCaQ_J9Atuk1q";

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// Any of these can sign into admin.html -- "internal UC staff", as opposed
// to a vendor login (role='vendor'), which always uses vendor.html. See
// AdminApp.vue for how each of the four maps to actual page/action access.
export const INTERNAL_ROLES = new Set(["admin", "management", "operations", "finance"]);

// Redirects to login.html if there's no active session; otherwise returns
// {session, profile}. Call this at the top of every protected page.
export async function requireSession() {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) {
    window.location.href = "login.html";
    return null;
  }
  const { data: profile, error } = await supabase
    .from("profiles")
    .select("*")
    .eq("id", session.user.id)
    .single();
  if (error || !profile) {
    await supabase.auth.signOut();
    window.location.href = "login.html";
    return null;
  }
  return { session, profile };
}
