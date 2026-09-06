import "./shared.css";
import { supabase, INTERNAL_ROLES } from "./supabaseClient.js";

(async () => {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) {
    window.location.href = "login.html";
    return;
  }
  const { data: profile } = await supabase.from("profiles").select("role").eq("id", session.user.id).single();
  window.location.href = INTERNAL_ROLES.has(profile?.role) ? "admin.html" : "vendor.html";
})();
