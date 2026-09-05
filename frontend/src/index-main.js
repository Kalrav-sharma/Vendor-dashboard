import "./shared.css";
import { supabase } from "./supabaseClient.js";

(async () => {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) {
    window.location.href = "login.html";
    return;
  }
  const { data: profile } = await supabase.from("profiles").select("role").eq("id", session.user.id).single();
  window.location.href = profile?.role === "admin" ? "admin.html" : "vendor.html";
})();
