// Internal UC-staff login accounts (profiles.role in 'management',
// 'operations', 'finance') -- admin-only, same shape as useVendors.js but
// calling the admin-manage-team Edge Function instead of admin-create-vendor.
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";

const TEAM_ROLES = ["management", "operations", "finance"];

export function useTeam() {
  const team = ref([]);

  async function refresh() {
    const { data, error } = await supabase
      .from("profiles").select("*").in("role", TEAM_ROLES).order("created_at", { ascending: false });
    if (!error) team.value = data;
    return { data, error };
  }

  async function callTeamAction(action, userId) {
    const { data, error } = await supabase.functions.invoke("admin-manage-team", {
      body: { action, user_id: userId },
    });
    if (error || data?.error) {
      let detail = data?.error || error?.message || "unexpected response from server";
      if (error?.context?.json) {
        try {
          const body = await error.context.json();
          if (body?.error) detail = body.error;
        } catch { /* body wasn't JSON -- keep whatever we already had */ }
      }
      return { ok: false, error: detail };
    }
    await refresh();
    return { ok: true };
  }

  const revokeTeamMember = (userId) => callTeamAction("revoke", userId);
  const restoreTeamMember = (userId) => callTeamAction("restore", userId);
  const deleteTeamMember = (userId) => callTeamAction("delete", userId);

  return { team, refresh, revokeTeamMember, restoreTeamMember, deleteTeamMember };
}
