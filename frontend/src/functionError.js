// Shared error-message extraction for supabase.functions.invoke() calls.
//
// On a non-2xx response, supabase-js returns { data: null, error } where
// error.message is just the generic "Edge Function returned a non-2xx
// status code" -- the actual { error: "..." } JSON body our functions send
// back is only reachable via error.context (a Response), which needs an
// async .json() call. Without this, the UI shows that generic message
// instead of the real reason (e.g. "duplicate email").
export async function resolveFunctionError(data, error) {
  let detail = data?.error || error?.message || "unexpected response from server";
  if (error?.context?.json) {
    try {
      const body = await error.context.json();
      if (body?.error) detail = body.error;
    } catch { /* body wasn't JSON -- keep whatever we already had */ }
  }
  return detail;
}
