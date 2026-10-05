-- 2026-10-01 security + load hardening -- run once in the Supabase SQL Editor.
-- The same changes are folded into schema.sql (the source of truth), so a later
-- full re-run of schema.sql keeps them. Idempotent, and all-or-nothing: if any
-- statement fails (including the 5s lock wait), nothing is applied.
begin;
set local lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Write privileges. Supabase grants anon/authenticated ALL on every public
--    table by default, so the column-level GRANTs this schema relied on
--    restricted nothing: a vendor could PATCH quantity/price on their own
--    po_items, or insert/patch their own invoice row as match_status='matched'
--    / payment_status='paid'. Table-level write privileges are revoked first
--    (that also drops any column-level ones), then exactly the columns the app
--    writes are granted back.
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
  all_tables text[] := array[
    'profiles', 'purchase_orders', 'grns', 'po_items', 'po_email_events', 'po_item_shipments',
    'shipment_tracking', 'grn_items', 'po_item_dispatch_changes', 'po_invoice_uploads',
    'mm_rate_card', 'vendor_contacts', 'sop_inventory_channel', 'sop_inventory_uc_warehouse',
    'sop_channel_drr_doi', 'sop_uniware_inventory', 'sop_dark_store_inventory', 'sop_facility_drr_doi',
    'sop_sales_plan_actual', 'sop_daily_sales', 'sop_production_daily', 'production_plan_snapshots',
    'sop_po_fulfillment_daily', 'sop_po_action_items', 'sop_po_shortfall_rca', 'sop_dispatch_plan',
    'sop_dispatch_production_check', 'sop_dispatch_pinned_date', 'sop_first_mile_plan',
    'sop_first_mile_plant', 'sop_first_mile_fill_rate', 'sop_first_mile_facility_util',
    'sop_first_mile_missed_po', 'last_mile_watchlist', 'last_mile_poll_state', 'last_mile_run',
    'last_mile_coverage', 'last_mile_dq_summary', 'last_mile_lsp_perf', 'last_mile_worst_lanes',
    'last_mile_alerts', 'last_mile_open_shipments', 'last_mile_sla_rules', 'sla_trend_weekly',
    'sla_rca_run', 'health_delay_weekly', 'sla_partner_otd_weekly', 'sla_pincode_alert',
    'sla_pincode_revised', 'spares_sku_master', 'spares_wh_inventory', 'spares_drr',
    'spares_status_override', 'spares_vendor_override', 'spares_category_override',
    'vendor_login_attempts', 'support_tickets'
  ];
  -- Browser-written tables whose authenticated grants are handled individually below.
  browser_written text[] := array[
    'po_items', 'po_item_dispatch_changes', 'po_invoice_uploads', 'support_tickets',
    'sla_pincode_revised', 'spares_status_override', 'spares_vendor_override', 'spares_category_override'
  ];
begin
  foreach t in array all_tables loop
    if to_regclass('public.' || t) is null then
      raise notice 'skipping missing table public.%', t;
      continue;
    end if;
    -- Nothing reads or writes as anon (every page signs in first; RLS already gave anon zero rows).
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke truncate, references, trigger on public.%I from authenticated', t);
    if not (t = any (browser_written)) then
      execute format('revoke insert, update, delete on public.%I from authenticated', t);
    end if;
  end loop;
end $$;

revoke all on public.vendor_login_attempts from authenticated;

revoke insert, update, delete on public.po_items from authenticated;
grant update (estimated_dispatch_date, estimated_dispatch_qty) on public.po_items to authenticated;

revoke insert, update, delete on public.po_item_dispatch_changes from authenticated;
grant insert (
  po_code, item_sku, vendor_code, changed_by,
  old_estimated_dispatch_date, old_estimated_dispatch_qty,
  new_estimated_dispatch_date, new_estimated_dispatch_qty, reason
) on public.po_item_dispatch_changes to authenticated;

revoke insert, update on public.po_invoice_uploads from authenticated;
grant insert (
  po_code, vendor_code, storage_path, file_name, file_size, uploaded_by, uploaded_by_name
) on public.po_invoice_uploads to authenticated;
grant update (
  credit_note_storage_path, credit_note_file_name, credit_note_file_size,
  credit_note_uploaded_by, credit_note_uploaded_by_name, credit_note_uploaded_at
) on public.po_invoice_uploads to authenticated;

revoke insert, update, delete on public.support_tickets from authenticated;
grant insert (
  vendor_code, vendor_name, category, po_code, subject, description, created_by, created_by_name
) on public.support_tickets to authenticated;
grant update (status, admin_response, responded_by_name, resolved_at, updated_at)
  on public.support_tickets to authenticated;

-- RPCs: signed-in callers only (each one still checks the caller itself).
revoke execute on function public.mark_password_changed() from public, anon;
grant execute on function public.mark_password_changed() to authenticated;
revoke execute on function public.confirm_dispatched(text, text, text, text) from public, anon;
grant execute on function public.confirm_dispatched(text, text, text, text) to authenticated;
revoke execute on function public.manual_confirm_dispatch(text, text, text, text, numeric, date) from public, anon;
grant execute on function public.manual_confirm_dispatch(text, text, text, text, numeric, date) to authenticated;

-- ---------------------------------------------------------------------------
-- 2. RLS policies.
--    - is_internal_staff()/auth.uid() wrapped in (select ...) so Postgres
--      evaluates them once per query instead of once per row (identical
--      result; OR'd with a column test they were being re-run on every row).
--    - Write policies now also pin the client-supplied columns that matter:
--      uploaded_by/created_by must be the caller, file paths must sit in the
--      row's own vendor folder (check-invoice-match downloads that path with
--      service_role), and dispatch-change rows must match the PO's vendor.
-- ---------------------------------------------------------------------------
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
  for select
  using (id = (select auth.uid()) or (select public.is_internal_staff()));

drop policy if exists purchase_orders_select on public.purchase_orders;
create policy purchase_orders_select on public.purchase_orders
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists grns_select on public.grns;
create policy grns_select on public.grns
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_items_select on public.po_items;
create policy po_items_select on public.po_items
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_items_update_dispatch on public.po_items;
create policy po_items_update_dispatch on public.po_items
  for update
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  )
  with check (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_item_shipments_select on public.po_item_shipments;
create policy po_item_shipments_select on public.po_item_shipments
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists shipment_tracking_select on public.shipment_tracking;
create policy shipment_tracking_select on public.shipment_tracking
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists grn_items_select on public.grn_items;
create policy grn_items_select on public.grn_items
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_item_dispatch_changes_select on public.po_item_dispatch_changes;
create policy po_item_dispatch_changes_select on public.po_item_dispatch_changes
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_item_dispatch_changes_insert on public.po_item_dispatch_changes;
create policy po_item_dispatch_changes_insert on public.po_item_dispatch_changes
  for insert
  with check (
    exists (
      select 1 from public.purchase_orders po
      where po.po_code = po_item_dispatch_changes.po_code and po.vendor_code = po_item_dispatch_changes.vendor_code
    )
    and (
      (select public.is_internal_staff())
      or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

drop policy if exists po_invoice_uploads_update_credit_note on public.po_invoice_uploads;
create policy po_invoice_uploads_update_credit_note on public.po_invoice_uploads
  for update
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  )
  with check (
    (credit_note_storage_path is null
      or left(credit_note_storage_path, length(vendor_code) + 1) = vendor_code || '/')
    and (credit_note_uploaded_by is null or credit_note_uploaded_by = (select auth.uid()))
    and (
      (select public.is_internal_staff())
      or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

drop policy if exists po_invoice_uploads_select on public.po_invoice_uploads;
create policy po_invoice_uploads_select on public.po_invoice_uploads
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_invoice_uploads_insert on public.po_invoice_uploads;
create policy po_invoice_uploads_insert on public.po_invoice_uploads
  for insert
  with check (
    exists (
      select 1 from public.purchase_orders po
      where po.po_code = po_invoice_uploads.po_code and po.vendor_code = po_invoice_uploads.vendor_code
    )
    and left(storage_path, length(vendor_code) + 1) = vendor_code || '/'
    and (uploaded_by is null or uploaded_by = (select auth.uid()))
    and (
      (select public.is_internal_staff())
      or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

drop policy if exists po_invoice_uploads_delete on public.po_invoice_uploads;
create policy po_invoice_uploads_delete on public.po_invoice_uploads
  for delete
  using (uploaded_by = (select auth.uid()) or (select public.is_internal_staff()));

drop policy if exists support_tickets_select on public.support_tickets;
create policy support_tickets_select on public.support_tickets
  for select
  using (
    (select public.is_internal_staff())
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists support_tickets_insert on public.support_tickets;
create policy support_tickets_insert on public.support_tickets
  for insert
  with check (
    (
      po_code is null
      or exists (
        select 1 from public.purchase_orders po
        where po.po_code = support_tickets.po_code and po.vendor_code = support_tickets.vendor_code
      )
    )
    and (created_by is null or created_by = (select auth.uid()))
    and (
      (select public.is_internal_staff())
      or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

-- ---------------------------------------------------------------------------
-- 3. Rate limiting for the Edge Functions (service_role only).
-- ---------------------------------------------------------------------------
create table if not exists public.api_rate_limits (
  bucket text primary key,
  window_start timestamptz not null default now(),
  hits int not null default 0
);
create index if not exists api_rate_limits_window_start_idx on public.api_rate_limits (window_start);
alter table public.api_rate_limits enable row level security;
revoke all on public.api_rate_limits from anon, authenticated;
grant select, insert, update, delete on public.api_rate_limits to service_role;

-- Fixed-window counter: true while `bucket` is within p_limit hits per window. p_cost 0 = check without counting.
create or replace function public.rate_limit_hit(p_bucket text, p_limit int, p_window_seconds int, p_cost int default 1)
returns boolean
language plpgsql
set search_path = public
as $$
declare
  v_window interval := make_interval(secs => p_window_seconds);
  v_hits int;
begin
  if p_cost <= 0 then
    select hits into v_hits from public.api_rate_limits
    where bucket = p_bucket and window_start > now() - v_window;
    return coalesce(v_hits, 0) < p_limit;
  end if;

  insert into public.api_rate_limits as r (bucket, window_start, hits)
  values (p_bucket, now(), p_cost)
  on conflict (bucket) do update set
    window_start = case when r.window_start <= now() - v_window then now() else r.window_start end,
    hits = case when r.window_start <= now() - v_window then p_cost else r.hits + p_cost end
  returning hits into v_hits;

  if random() < 0.01 then
    delete from public.api_rate_limits where window_start < now() - interval '1 day';
  end if;

  return v_hits <= p_limit;
end;
$$;
revoke all on function public.rate_limit_hit(text, int, int, int) from public, anon, authenticated;
grant execute on function public.rate_limit_hit(text, int, int, int) to service_role;

-- Atomic replacement for vendor-code-auth's read-then-write failure counter (parallel guesses all read the same count and never reached the lockout).
create or replace function public.vendor_login_record_failure(p_vendor_code text, p_max_attempts int, p_lockout_minutes int)
returns void
language sql
set search_path = public
as $$
  insert into public.vendor_login_attempts as a (vendor_code, failed_count, locked_until)
  values (
    p_vendor_code,
    case when p_max_attempts <= 1 then 0 else 1 end,
    case when p_max_attempts <= 1 then now() + make_interval(mins => p_lockout_minutes) end
  )
  on conflict (vendor_code) do update set
    failed_count = case when a.failed_count + 1 >= p_max_attempts then 0 else a.failed_count + 1 end,
    locked_until = case
      when a.failed_count + 1 >= p_max_attempts then now() + make_interval(mins => p_lockout_minutes)
      when a.locked_until > now() then a.locked_until
    end;
$$;
revoke all on function public.vendor_login_record_failure(text, int, int) from public, anon, authenticated;
grant execute on function public.vendor_login_record_failure(text, int, int) to service_role;

-- ---------------------------------------------------------------------------
-- 4. Indexes for the app's hot queries (all small tables -- builds take
--    seconds at most; they briefly block writes, never reads).
-- ---------------------------------------------------------------------------
create index if not exists purchase_orders_created_at_idx on public.purchase_orders (created_at desc);
create index if not exists po_item_shipments_vendor_code_idx on public.po_item_shipments (vendor_code, confirmed_at desc);
create index if not exists po_item_shipments_confirmed_at_idx on public.po_item_shipments (confirmed_at desc);
create index if not exists po_invoice_uploads_created_at_idx on public.po_invoice_uploads (created_at desc);
create index if not exists support_tickets_created_at_idx on public.support_tickets (created_at desc);
create index if not exists profiles_role_created_at_idx on public.profiles (role, created_at desc);
create index if not exists idx_sop_po_action_items_run on public.sop_po_action_items (run_date);
create index if not exists idx_sop_po_shortfall_rca_run on public.sop_po_shortfall_rca (run_date);
create index if not exists idx_sop_dispatch_production_check_run on public.sop_dispatch_production_check (run_date, view_key);
create index if not exists idx_last_mile_open_shipments_overdue on public.last_mile_open_shipments (run_id, days_overdue desc);
create index if not exists idx_last_mile_alerts_overdue on public.last_mile_alerts (run_id, days_overdue desc);
create index if not exists idx_health_delay_weekly_week on public.health_delay_weekly (week_start);

-- vendor_code doubles as a login identifier, so it must be unique among vendor
-- logins (the admin functions check first; this closes the race). Skipped with
-- a notice if duplicates already exist -- vendor-code sign-in is already broken
-- for those codes, so resolve them and re-run.
do $$
begin
  if exists (
    select 1 from public.profiles
    where role = 'vendor' and vendor_code is not null
    group by vendor_code having count(*) > 1
  ) then
    raise notice 'duplicate vendor codes in profiles -- profiles_vendor_code_unique_idx NOT created';
  else
    create unique index if not exists profiles_vendor_code_unique_idx
      on public.profiles (vendor_code) where role = 'vendor';
  end if;
end $$;

commit;
