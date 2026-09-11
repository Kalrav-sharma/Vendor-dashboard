// Internal UC-staff login accounts (profiles.role in 'admin', 'management',
// 'operations', 'finance') -- admin-only, same shape as useVendors.js but
// calling the admin-manage-team Edge Function instead of admin-create-vendor.
//
// Includes 'admin' rows purely so Manage Access can SHOW who currently has
// admin -- revoke/restore/delete still only ever call admin-manage-team,
// which refuses to act on an admin row (that function's own guard, kept
// deliberately narrow); ManageAccess.vue only ever offers those actions
// for non-admin rows. Admin rows are otherwise only reachable through
// "Edit access" (admin-change-access), which can move a login to/from
// 'admin' but was never meant to also take over revoke/restore/delete.
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";
import { resolveFunctionError } from "../functionError.js";

const TEAM_ROLES = ["admin", "management", "operations", "finance"];

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
      return { ok: false, error: await resolveFunctionError(data, error) };
    }
    await refresh();
    return { ok: true };
  }

  const revokeTeamMember = (userId) => callTeamAction("revoke", userId);
  const restoreTeamMember = (userId) => callTeamAction("restore", userId);
  const deleteTeamMember = (userId) => callTeamAction("delete", userId);

  return { team, refresh, revokeTeamMember, restoreTeamMember, deleteTeamMember };
}
