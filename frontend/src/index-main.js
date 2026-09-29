import "./shared.css";
import { supabase, INTERNAL_ROLES, isRecoveryLink } from "./supabaseClient.js";

(async () => {
  if (isRecoveryLink()) {
    window.location.href = "reset-password.html" + window.location.hash;
    return;
  }
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) {
    window.location.href = "login.html";
    return;
  }
  const { data: profile } = await supabase.from("profiles").select("role").eq("id", session.user.id).single();
  window.location.href = INTERNAL_ROLES.has(profile?.role) ? "admin.html" : "vendor.html";
})();
