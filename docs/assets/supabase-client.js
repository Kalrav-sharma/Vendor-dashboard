// Shared Supabase client config for every page in docs/.
//
// Both values below are the PUBLIC project URL and the `anon` `public` key
// -- these are meant to be shipped in client-side code and are safe to
// commit. They grant no access on their own; every table has Row Level
// Security enabled (see supabase/schema.sql), so what a logged-in user can
// actually read is enforced by Postgres, not by this file.
//
// Never put the service_role key here or in any file under docs/ -- it
// bypasses RLS entirely and must only ever exist as a GitHub Actions
// secret and a Supabase Edge Function secret.

const SUPABASE_URL = "https://jfxfzulufaxrmopnvpqa.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_LNoy7fE1VMV1V7ygcUhCaQ_J9Atuk1q";

function getSupabaseClient() {
  return window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}

// Redirects to login.html if there's no active session; otherwise returns
// {client, session, profile}. Call this at the top of every protected page.
async function requireSession() {
  const client = getSupabaseClient();
  const { data: { session } } = await client.auth.getSession();
  if (!session) {
    window.location.href = "login.html";
    return null;
  }
  const { data: profile, error } = await client
    .from("profiles")
    .select("*")
    .eq("id", session.user.id)
    .single();
  if (error || !profile) {
    await client.auth.signOut();
    window.location.href = "login.html";
    return null;
  }
  return { client, session, profile };
}
