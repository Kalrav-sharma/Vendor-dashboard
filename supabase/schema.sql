-- Lexcru / vendor PO tracker — Supabase schema + Row Level Security
--
-- Run this once in the Supabase SQL Editor (Project → SQL Editor → New query)
-- right after creating the project. Safe to re-run (uses IF NOT EXISTS /
-- CREATE OR REPLACE / DROP POLICY IF EXISTS throughout).
--
-- Data model:
--   profiles          — one row per login, 1:1 with auth.users via id.
--                        role is one of:
--                          'vendor'     — scoped to vendor_code (their own
--                                         data only)
--                          'admin'      — full access to everything,
--                                         including creating new logins
--                                         (vendor AND internal-staff)
--                          'management' — full access to everything EXCEPT
--                                         creating new logins
--                          'operations' — PO Tracking + SKU Level Data only,
--                                         across all vendors
--                          'finance'    — Payment Dashboard only, across all
--                                         vendors
--                        vendor_code is null for every role except 'vendor'
--                        — that's the only thing that scopes what a vendor
--                        can see. The other four roles are collectively
--                        "internal staff" (see is_internal_staff() below)
--                        and are never scoped to one vendor.
--   purchase_orders   — one row per PO, written only by the GitHub Actions
--                        pipeline (via the service_role key, which bypasses
--                        RLS entirely — there is deliberately no INSERT/
--                        UPDATE policy for normal logins on this table).
--   grns              — one row per GRN, same write path as purchase_orders.
--                        vendor_code is denormalized here (copied from the
--                        parent PO) purely so its RLS policy doesn't need a
--                        join, not because it can differ from the PO's.
--   po_items           — one row per SKU line item on a PO (qty ordered/
--                        received/pending/rejected, pricing). Same write
--                        path and denormalized vendor_code as purchase_orders.
--   grn_items          — one row per SKU line item on a GRN. Same pattern.
--
-- Access model:
--   - A vendor login can SELECT only rows whose vendor_code matches their
--     own profile's vendor_code.
--   - Any internal-staff login (admin/management/operations/finance) can
--     SELECT everything -- RLS itself doesn't distinguish between these
--     four; which PAGES each one actually sees is enforced in the frontend
--     (AdminApp.vue), and which one can CREATE new logins is enforced in
--     the admin-create-vendor / admin-manage-team Edge Functions.
--   - Nobody except service_role can INSERT/UPDATE/DELETE anywhere — all
--     writes happen server-side (the Actions pipeline for PO/GRN data, the
--     admin Edge Functions for vendor/internal-staff accounts).

-- ---------------------------------------------------------------------
-- profiles
--
-- Created before is_admin() below: is_admin() is a LANGUAGE SQL function,
-- and unlike plpgsql, Postgres validates a SQL-language function body
-- (including that referenced tables actually exist) at CREATE FUNCTION
-- time, not just at call time. It has to be defined after profiles.
-- ---------------------------------------------------------------------
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  role text not null check (role in ('admin', 'vendor', 'management', 'operations', 'finance')),
  vendor_code text,          -- required for role='vendor', null for every other role
  vendor_name text,          -- display name only -- e.g. "LEXCRU WATER TECH PVT LTD" for a
                              -- vendor, or just the person's name for internal staff
  email text,                -- denormalized copy of auth.users.email, for admin display only
  contact_name text,         -- the actual person at the vendor this login is for
  contact_mobile text,
  revoked boolean not null default false,  -- display-only mirror of the real
                              -- enforcement, which is a Supabase Auth ban set
                              -- server-side by the admin-create-vendor Edge
                              -- Function -- this column can't itself block
                              -- login, it only lets the admin console show
                              -- correct Revoke/Restore state without needing
                              -- service_role access to check it
  must_change_password boolean not null default false,  -- true for a vendor
                              -- login just created with the shared default
                              -- temp password (see admin-create-vendor's
                              -- DEFAULT_TEMP_PASSWORD) -- the frontend gates
                              -- on this to force a real password before
                              -- showing anything else; cleared by
                              -- mark_password_changed() once they set one
  created_at timestamptz not null default now()
);

-- profiles already existed before these columns were added, so "create
-- table if not exists" above won't retroactively add them on an
-- already-deployed database -- these do, and are a no-op if already there.
alter table public.profiles add column if not exists revoked boolean not null default false;
alter table public.profiles add column if not exists contact_name text;
alter table public.profiles add column if not exists contact_mobile text;
alter table public.profiles add column if not exists must_change_password boolean not null default false;

-- profiles.role's check constraint was originally just ('admin', 'vendor')
-- -- widen it for an already-deployed database (Postgres auto-names an
-- inline column check constraint "<table>_<column>_check", so this is safe
-- to target by that name). A no-op on a fresh install, which already gets
-- the wider constraint from the create table above.
alter table public.profiles drop constraint if exists profiles_role_check;
alter table public.profiles add constraint profiles_role_check
  check (role in ('admin', 'vendor', 'management', 'operations', 'finance'));

-- SECURITY DEFINER so a vendor can clear their OWN must_change_password flag
-- without needing a general UPDATE grant on profiles (which stays
-- service_role/admin-only otherwise -- see the comment below the profiles
-- RLS policy). Called from SetNewPasswordForm.vue right after
-- supabase.auth.updateUser({password}) succeeds, whether that happened via
-- the forced first-login change or the "forgot password" email-link flow --
-- either way, the vendor just set a real password of their own.
create or replace function public.mark_password_changed()
returns void
language sql
security definer
set search_path = public
as $$
  update public.profiles set must_change_password = false where id = auth.uid();
$$;
grant execute on function public.mark_password_changed() to authenticated;

-- ---------------------------------------------------------------------
-- is_admin(): SECURITY DEFINER so it can check profiles.role without
-- re-triggering profiles' own RLS policy (querying a table from inside its
-- own policy causes "infinite recursion detected in policy" in Postgres).
-- ---------------------------------------------------------------------
create or replace function public.is_admin()
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and role = 'admin'
  );
$$;

-- ---------------------------------------------------------------------
-- is_internal_staff(): true for every UC-employee role (admin, management,
-- operations, finance) — none of these are scoped to a single vendor_code,
-- so every RLS policy that used to say "is_admin() or own vendor_code" now
-- says "is_internal_staff() or own vendor_code" instead, letting all four
-- see across every vendor. is_admin() above stays narrowly 'admin' only —
-- nothing at the RLS layer currently needs that narrower check (which of
-- these four roles can CREATE new logins, and which pages each one sees,
-- are both enforced elsewhere: admin-create-vendor/admin-manage-team for
-- the former, AdminApp.vue for the latter).
-- ---------------------------------------------------------------------
create or replace function public.is_internal_staff()
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and role in ('admin', 'management', 'operations', 'finance')
  );
$$;

alter table public.profiles enable row level security;

drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
  for select
  using (id = auth.uid() or public.is_internal_staff());

-- No insert/update/delete policy for anon/authenticated on purpose: vendor
-- accounts are created only via the admin Edge Function, which uses the
-- service_role key and so bypasses RLS entirely.

-- ---------------------------------------------------------------------
-- purchase_orders
-- ---------------------------------------------------------------------
create table if not exists public.purchase_orders (
  po_code text primary key,
  facility text not null,
  vendor_code text not null,
  vendor_name text,          -- Uniware masks this in its API, so the sync
                              -- script fills it in from its own known
                              -- vendor_code -> vendor_name mapping instead.
  status text,
  created_at timestamptz,
  total_amount numeric,
  qty_ordered numeric,
  qty_received numeric,
  qty_pending numeric,
  qty_rejected numeric,
  num_items integer,
  updated_at timestamptz not null default now()
);

-- purchase_orders already existed before vendor_name was added, so
-- "create table if not exists" above won't retroactively add the column
-- on an already-deployed database -- this does, and is a no-op if it's
-- already there.
alter table public.purchase_orders add column if not exists vendor_name text;

create index if not exists purchase_orders_vendor_code_idx on public.purchase_orders(vendor_code);

alter table public.purchase_orders enable row level security;

drop policy if exists purchase_orders_select on public.purchase_orders;
create policy purchase_orders_select on public.purchase_orders
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- ---------------------------------------------------------------------
-- grns
-- ---------------------------------------------------------------------
create table if not exists public.grns (
  grn_code text primary key,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  vendor_code text not null,  -- denormalized from the parent PO, for a join-free RLS check
  status text,
  created_at timestamptz,
  vendor_invoice_number text,
  vendor_invoice_date date,
  total_received_amount numeric,
  total_rejected_amount numeric,
  updated_at timestamptz not null default now()
);

create index if not exists grns_po_code_idx on public.grns(po_code);
create index if not exists grns_vendor_code_idx on public.grns(vendor_code);

alter table public.grns enable row level security;

drop policy if exists grns_select on public.grns;
create policy grns_select on public.grns
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- ---------------------------------------------------------------------
-- po_items — one row per SKU line item on a PO. Powers the "SKU Level
-- Data" section (clicking a PO in PO Tracking filters this by po_code).
-- ---------------------------------------------------------------------
create table if not exists public.po_items (
  id bigint generated always as identity primary key,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  vendor_code text not null,  -- denormalized from the parent PO, for a join-free RLS check
  item_sku text not null,
  item_name text,
  quantity numeric,
  received_quantity numeric,
  pending_quantity numeric,
  rejected_quantity numeric,
  unit_price numeric,
  max_retail_price numeric,
  subtotal numeric,
  total numeric,
  updated_at timestamptz not null default now(),
  unique (po_code, item_sku)
);

-- po_items already existed before the Dispatch Planning columns were
-- added, so "create table if not exists" above won't retroactively add
-- them on an already-deployed database -- these do, and are a no-op if
-- already there. Vendor-entered, per SKU: see the "Dispatch Planning"
-- feature (PoDetailModal.vue's new input columns, DispatchPlanningTable.vue,
-- scripts/send_po_emails.py's reminder logic).
alter table public.po_items add column if not exists estimated_dispatch_date date;
alter table public.po_items add column if not exists estimated_dispatch_qty numeric;

create index if not exists po_items_po_code_idx on public.po_items(po_code);
create index if not exists po_items_vendor_code_idx on public.po_items(vendor_code);

alter table public.po_items enable row level security;

drop policy if exists po_items_select on public.po_items;
create policy po_items_select on public.po_items
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- Dispatch Planning write path: a vendor (own vendor_code) or any
-- internal-staff login can update po_items -- but a column-level GRANT
-- (layered under RLS, which only controls ROWS) restricts what they can
-- actually touch to just the two estimate columns, so this can never be
-- used to edit quantity/pricing/etc even if a buggy or malicious client
-- included those fields in its update payload.
grant update (estimated_dispatch_date, estimated_dispatch_qty) on public.po_items to authenticated;

drop policy if exists po_items_update_dispatch on public.po_items;
create policy po_items_update_dispatch on public.po_items
  for update
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  )
  with check (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- ---------------------------------------------------------------------
-- po_email_events — outbox + audit log for the Dispatch Planning email
-- automation (see scripts/send_po_emails.py). Three kinds of row:
--   - 'new_po'        -- queued automatically by the trigger below, exactly
--     once per genuinely NEW purchase order (never on a resync update of
--     an existing one -- see queue_new_po_email()'s AFTER INSERT trigger).
--     item_sku is null -- applies to the whole PO. sent_at is null until
--     send_po_emails.py actually sends it.
--   - 'next_dispatch' -- queued by confirm_dispatched() below when
--     Operations confirms a SKU's estimated dispatch and that SKU still
--     has pending_quantity > 0 -- tells the vendor to provide a fresh
--     estimate for the remaining balance. item_sku is set (unlike
--     'new_po', this is SKU-specific, since a PO can have some SKUs fully
--     dispatched and others still pending at different times).
--   - 'reminder'      -- inserted directly by send_po_emails.py itself (no
--     trigger), one per PO per calendar day (IST) it sends a reminder --
--     used both as the audit log and as that script's own "already
--     reminded today" dedup check.
-- 'new_po' and 'next_dispatch' both anchor send_po_emails.py's 7-day-then-
-- daily reminder cascade for the SKU(s) they cover -- see that script's
-- process_reminders() for how a SKU's EFFECTIVE anchor is whichever of the
-- two is more recent (the PO-level 'new_po', or that SKU's own later
-- 'next_dispatch' if one exists).
-- Nobody but service_role (the trigger and confirm_dispatched() both run
-- SECURITY DEFINER; the script itself uses the service_role key) ever
-- writes this -- internal staff can only read it, for visibility into
-- what's been sent.
-- ---------------------------------------------------------------------
create table if not exists public.po_email_events (
  id bigint generated always as identity primary key,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  vendor_code text not null,
  event_type text not null check (event_type in ('new_po', 'next_dispatch', 'reminder')),
  item_sku text,  -- set for 'next_dispatch' (SKU-specific); null for 'new_po'/'reminder' (whole-PO)
  queued_at timestamptz not null default now(),
  sent_at timestamptz,
  error text
);

-- po_email_events already existed before item_sku was added (the
-- 'next_dispatch' event type), so "create table if not exists" above
-- won't retroactively add it on an already-deployed database -- this
-- does, and is a no-op if already there.
alter table public.po_email_events add column if not exists item_sku text;

-- Same reason -- the CHECK constraint originally only allowed
-- ('new_po', 'reminder'); widen it for an already-deployed database the
-- same way profiles_role_check was widened above.
alter table public.po_email_events drop constraint if exists po_email_events_event_type_check;
alter table public.po_email_events add constraint po_email_events_event_type_check
  check (event_type in ('new_po', 'next_dispatch', 'reminder'));

create index if not exists po_email_events_po_code_idx on public.po_email_events(po_code);
create index if not exists po_email_events_pending_idx on public.po_email_events(event_type, sent_at);

alter table public.po_email_events enable row level security;

drop policy if exists po_email_events_select on public.po_email_events;
create policy po_email_events_select on public.po_email_events
  for select
  using (public.is_internal_staff());  -- vendors never see this -- it's an internal audit log, not a feature

-- SECURITY DEFINER so this can insert regardless of who/what triggered the
-- underlying purchase_orders write (normally service_role via the Uniware
-- sync, which bypasses RLS anyway, but this makes the trigger's own insert
-- unconditional rather than depending on the inserting role's own grants).
create or replace function public.queue_new_po_email()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Only queue a notification for a PO actually created recently -- without
  -- this, backfilling a brand-new vendor's entire multi-week PO history
  -- (a real, expected code path -- see sync_to_supabase.py's needs_backfill)
  -- would fire one "new PO" email per historical PO all at once. 4 days
  -- matches that same script's own RECENT_WINDOW_DAYS for the same reason.
  if new.created_at is not null and new.created_at >= now() - interval '4 days' then
    insert into public.po_email_events (po_code, vendor_code, event_type)
    values (new.po_code, new.vendor_code, 'new_po');
  end if;
  return new;
end;
$$;

drop trigger if exists trg_queue_new_po_email on public.purchase_orders;
create trigger trg_queue_new_po_email
  after insert on public.purchase_orders
  for each row
  execute function public.queue_new_po_email();

-- ---------------------------------------------------------------------
-- po_item_shipments — one row per CONFIRMED dispatch (every "Dispatched"
-- click on a Dispatch Planning row), regardless of whether that SKU still
-- has a balance pending afterward. This is deliberately a log, not a
-- single overwritten field on po_items, since one SKU can be dispatched in
-- several partial batches over its lifecycle (see confirm_dispatched()'s
-- reset-for-the-remaining-balance behavior) -- each batch is its own
-- shipment with its own AWB. This is also the intended anchor for the
-- planned AWB-tracking integration (checking live status/ETA per
-- shipment) -- not built yet, but this table is where that would read
-- its AWB list from.
-- ---------------------------------------------------------------------
create table if not exists public.po_item_shipments (
  id bigint generated always as identity primary key,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  item_sku text not null,
  vendor_code text not null,  -- denormalized, for a join-free RLS check
  awb_number text not null,
  dispatched_qty numeric,   -- the estimated_dispatch_qty in effect at confirmation time
  dispatched_date date,     -- the estimated_dispatch_date in effect at confirmation time
  confirmed_by text,        -- display name of whoever clicked "Dispatched"
  confirmed_at timestamptz not null default now()
);

create index if not exists po_item_shipments_po_code_idx on public.po_item_shipments(po_code);
create index if not exists po_item_shipments_awb_idx on public.po_item_shipments(awb_number);

-- po_item_shipments already existed before DTDC support -- these two
-- statements retroactively add the courier column on an already-deployed
-- database (no-op if already there). 'bluedart' | 'dtdc', which tracking
-- API owns this AWB -- the default backfills the Bluedart-only rows
-- created before DTDC existed; every new row sets it explicitly
-- (Dispatch Planning's courier dropdown -- see confirm_dispatched()).
alter table public.po_item_shipments add column if not exists courier text not null default 'bluedart';
create index if not exists po_item_shipments_courier_idx on public.po_item_shipments(courier);

alter table public.po_item_shipments enable row level security;

drop policy if exists po_item_shipments_select on public.po_item_shipments;
create policy po_item_shipments_select on public.po_item_shipments
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );
-- No insert/update/delete policy for authenticated -- only
-- confirm_dispatched() (SECURITY DEFINER) below ever writes this.

-- ---------------------------------------------------------------------
-- shipment_tracking -- live courier status per AWB, one row per unique
-- (courier, awb_number) pair (NOT per po_item_shipments row -- several
-- SKUs dispatched together under one physical package share one AWB, so
-- this stays a separate table keyed by awb rather than columns bolted
-- onto po_item_shipments). Keyed on (courier, awb_number) together, not
-- awb_number alone, since AWB numbers are only unique within one
-- courier's own numbering -- two different couriers could in principle
-- issue the same number. vendor_code is denormalized here too, same
-- join-free-RLS-check reason as po_item_shipments.vendor_code above.
--
-- Populated by scripts/sync_bluedart_tracking.py (Bluedart's legacy
-- Track & Trace API, LoginID + LicenceKey auth) and
-- scripts/sync_dtdc_tracking.py (DTDC's tracking API) on a schedule via
-- GitHub Actions -- never written from the browser, so there's no
-- insert/update policy for authenticated. status_type/status_text/raw are
-- deliberately generic (each courier has its own status-code vocabulary
-- and response shape) -- see BluedartStatusChip.vue/DtdcStatusChip.vue on
-- the frontend for how each courier's own codes get a friendly label.
-- ---------------------------------------------------------------------
create table if not exists public.shipment_tracking (
  awb_number text not null,
  courier text not null default 'bluedart',  -- 'bluedart' | 'dtdc'
  vendor_code text,
  status_type text,          -- the courier's own raw status code
  status_text text,          -- human-readable status, e.g. "In Transit. Await delivery information"
  origin text,
  destination text,
  expected_delivery_date date,
  last_scan_text text,
  last_scan_location text,
  last_scan_at timestamptz,
  raw jsonb,                 -- full parsed courier response, for fields not modeled above
  updated_at timestamptz not null default now(),
  primary key (courier, awb_number)
);

create index if not exists shipment_tracking_vendor_code_idx on public.shipment_tracking(vendor_code);

-- shipment_tracking already existed as a Bluedart-only table (primary key
-- on awb_number alone) before DTDC support -- these retroactively widen
-- it on an already-deployed database (no-op on a fresh install, where the
-- create table above already has the right shape).
alter table public.shipment_tracking add column if not exists courier text not null default 'bluedart';
alter table public.shipment_tracking drop constraint if exists shipment_tracking_pkey;
alter table public.shipment_tracking add primary key (courier, awb_number);

alter table public.shipment_tracking enable row level security;

drop policy if exists shipment_tracking_select on public.shipment_tracking;
create policy shipment_tracking_select on public.shipment_tracking
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );
-- No insert/update/delete policy for authenticated -- only
-- scripts/sync_bluedart_tracking.py (service_role key) ever writes this.

-- ---------------------------------------------------------------------
-- confirm_dispatched(po_code, item_sku, awb_number, courier): called from
-- Dispatch Planning's "Dispatched" button (internal staff only) when
-- Operations confirms a SKU's estimated dispatch actually went out.
-- awb_number is mandatory (Kalrav's explicit spec) -- rejected server-side
-- too, not just in the UI, since RLS/grants alone can't enforce a
-- required-field rule on an RPC call. courier is mandatory too, once
-- DTDC joined Bluedart as a second tracked courier (Kalrav's call: an
-- explicit dropdown in the UI, not guessed from the AWB's format) --
-- it's what tells scripts/sync_bluedart_tracking.py /
-- sync_dtdc_tracking.py which AWBs are theirs to poll.
--
-- Always logs the shipment (po_item_shipments above) first, then:
--   - If that SKU still has pending_quantity > 0 (Uniware's own synced
--     figure -- not something this feature tracks itself): clears its
--     estimated_dispatch_date/qty back to null (so it drops off Dispatch
--     Planning and the PO popup asks for a fresh estimate) and queues a
--     'next_dispatch' email telling the vendor to provide one for the
--     remaining balance.
--   - If pending_quantity is already 0: does nothing further here -- the
--     SKU is fully dispatched, and PoDetailModal.vue's own pending<=0
--     check independently blocks further input on it, so there's nothing
--     left to reset or notify about.
--
-- SECURITY DEFINER so it can write po_items/po_email_events/
-- po_item_shipments regardless of the caller's own grants (mirrors
-- queue_new_po_email() above) -- the is_internal_staff() check inside is
-- what actually gates who can call this meaningfully, since RLS can't
-- gate an RPC call itself.
-- ---------------------------------------------------------------------
drop function if exists public.confirm_dispatched(text, text);
drop function if exists public.confirm_dispatched(text, text, text);

create or replace function public.confirm_dispatched(p_po_code text, p_item_sku text, p_awb_number text, p_courier text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_vendor_code text;
  v_pending numeric;
  v_qty numeric;
  v_date date;
  v_by text;
begin
  if not public.is_internal_staff() then
    raise exception 'Internal staff access required';
  end if;

  if p_awb_number is null or length(trim(p_awb_number)) = 0 then
    raise exception 'AWB/Tracking ID is required';
  end if;

  if p_courier not in ('bluedart', 'dtdc') then
    raise exception 'courier must be one of: bluedart, dtdc';
  end if;

  select vendor_code, pending_quantity, estimated_dispatch_qty, estimated_dispatch_date
    into v_vendor_code, v_pending, v_qty, v_date
  from public.po_items
  where po_code = p_po_code and item_sku = p_item_sku;

  if v_vendor_code is null then
    raise exception 'PO item not found: % / %', p_po_code, p_item_sku;
  end if;

  select coalesce(vendor_name, email) into v_by from public.profiles where id = auth.uid();

  insert into public.po_item_shipments (po_code, item_sku, vendor_code, awb_number, courier, dispatched_qty, dispatched_date, confirmed_by)
  values (p_po_code, p_item_sku, v_vendor_code, trim(p_awb_number), p_courier, v_qty, v_date, v_by);

  if coalesce(v_pending, 0) > 0 then
    update public.po_items
    set estimated_dispatch_date = null, estimated_dispatch_qty = null
    where po_code = p_po_code and item_sku = p_item_sku;

    insert into public.po_email_events (po_code, vendor_code, event_type, item_sku)
    values (p_po_code, v_vendor_code, 'next_dispatch', p_item_sku);
  end if;
end;
$$;

grant execute on function public.confirm_dispatched(text, text, text, text) to authenticated;

-- ---------------------------------------------------------------------
-- manual_confirm_dispatch(po_code, item_sku, awb_number, courier,
-- dispatched_qty, dispatched_date): the OTHER way a shipment gets logged
-- -- for when a vendor never gave an estimate at all (no "Awaiting
-- dispatch" row ever appeared for this SKU) but the dispatch actually
-- happened anyway and Ops needs to record it after the fact. Called from
-- Dispatch Planning's "+ Manual Dispatch" button (internal staff only,
-- ManualDispatchModal.vue): Ops types a PO code, sees every SKU on it
-- that's still pending, and enters the real quantity + AWB/courier
-- actually dispatched for whichever of those they're recording now.
--
-- Unlike confirm_dispatched(), the quantity and date are supplied
-- directly rather than read off po_items' vendor-entered estimate
-- (there may never have been one) -- validated against the SKU's real
-- pending_quantity server-side so this can't silently over-log a
-- shipment. Same downstream behavior otherwise: if a balance remains
-- pending after this entry, clears any stale estimate and queues a
-- 'next_dispatch' email so the vendor is asked for one on the
-- remainder, exactly like the normal flow.
--
-- SECURITY DEFINER for the same reason as confirm_dispatched() --
-- is_internal_staff() inside is what actually gates this, since RLS
-- can't gate an RPC call.
-- ---------------------------------------------------------------------
create or replace function public.manual_confirm_dispatch(
  p_po_code text, p_item_sku text, p_awb_number text, p_courier text,
  p_dispatched_qty numeric, p_dispatched_date date
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_vendor_code text;
  v_pending numeric;
  v_by text;
begin
  if not public.is_internal_staff() then
    raise exception 'Internal staff access required';
  end if;

  if p_awb_number is null or length(trim(p_awb_number)) = 0 then
    raise exception 'AWB/Tracking ID is required';
  end if;

  if p_courier not in ('bluedart', 'dtdc') then
    raise exception 'courier must be one of: bluedart, dtdc';
  end if;

  if p_dispatched_qty is null or p_dispatched_qty <= 0 then
    raise exception 'Dispatched quantity must be greater than zero';
  end if;

  if p_dispatched_date is null then
    raise exception 'Dispatched date is required';
  end if;

  select vendor_code, pending_quantity into v_vendor_code, v_pending
  from public.po_items
  where po_code = p_po_code and item_sku = p_item_sku;

  if v_vendor_code is null then
    raise exception 'PO item not found: % / %', p_po_code, p_item_sku;
  end if;

  if p_dispatched_qty > coalesce(v_pending, 0) then
    raise exception 'Dispatched quantity (%) exceeds pending quantity (%)', p_dispatched_qty, coalesce(v_pending, 0);
  end if;

  select coalesce(vendor_name, email) into v_by from public.profiles where id = auth.uid();

  insert into public.po_item_shipments (po_code, item_sku, vendor_code, awb_number, courier, dispatched_qty, dispatched_date, confirmed_by)
  values (p_po_code, p_item_sku, v_vendor_code, trim(p_awb_number), p_courier, p_dispatched_qty, p_dispatched_date, v_by);

  if p_dispatched_qty < coalesce(v_pending, 0) then
    update public.po_items
    set estimated_dispatch_date = null, estimated_dispatch_qty = null
    where po_code = p_po_code and item_sku = p_item_sku;

    insert into public.po_email_events (po_code, vendor_code, event_type, item_sku)
    values (p_po_code, v_vendor_code, 'next_dispatch', p_item_sku);
  end if;
end;
$$;

grant execute on function public.manual_confirm_dispatch(text, text, text, text, numeric, date) to authenticated;

-- ---------------------------------------------------------------------
-- grn_items — one row per SKU line item on a GRN. Same purpose as
-- po_items, for the receipt side of the SKU-level breakdown.
-- ---------------------------------------------------------------------
create table if not exists public.grn_items (
  id bigint generated always as identity primary key,
  grn_code text not null references public.grns(grn_code) on delete cascade,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  vendor_code text not null,  -- denormalized, for a join-free RLS check
  item_sku text not null,
  item_name text,
  quantity numeric,           -- received quantity for this SKU on this GRN
  rejected_quantity numeric,
  unit_price numeric,
  updated_at timestamptz not null default now(),
  unique (grn_code, item_sku)
);

create index if not exists grn_items_grn_code_idx on public.grn_items(grn_code);
create index if not exists grn_items_po_code_idx on public.grn_items(po_code);
create index if not exists grn_items_vendor_code_idx on public.grn_items(vendor_code);

alter table public.grn_items enable row level security;

drop policy if exists grn_items_select on public.grn_items;
create policy grn_items_select on public.grn_items
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- ---------------------------------------------------------------------
-- po_item_dispatch_changes — audit log of every CHANGE (never the first
-- entry) to a po_items row's estimated_dispatch_date/qty. PoDetailModal.vue
-- requires a mandatory reason + explicit confirmation before overwriting an
-- already-set value (Kalrav's explicit spec), and this is where that
-- reason gets recorded -- one row per confirmed change, old + new values
-- both captured so the full history is reconstructable without needing
-- po_items' own (overwritten) values.
-- ---------------------------------------------------------------------
create table if not exists public.po_item_dispatch_changes (
  id bigint generated always as identity primary key,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  item_sku text not null,
  vendor_code text not null,  -- denormalized, for a join-free RLS check
  changed_by text,            -- display name of whoever made the change (uploaderLabel)
  old_estimated_dispatch_date date,
  old_estimated_dispatch_qty numeric,
  new_estimated_dispatch_date date,
  new_estimated_dispatch_qty numeric,
  reason text not null,
  changed_at timestamptz not null default now()
);

create index if not exists po_item_dispatch_changes_po_code_idx on public.po_item_dispatch_changes(po_code);

alter table public.po_item_dispatch_changes enable row level security;

drop policy if exists po_item_dispatch_changes_select on public.po_item_dispatch_changes;
create policy po_item_dispatch_changes_select on public.po_item_dispatch_changes
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

drop policy if exists po_item_dispatch_changes_insert on public.po_item_dispatch_changes;
create policy po_item_dispatch_changes_insert on public.po_item_dispatch_changes
  for insert
  with check (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- ---------------------------------------------------------------------
-- po_invoice_uploads — vendor-uploaded invoice copy files (dispatch
-- documentation), distinct from grns.vendor_invoice_number (a Uniware
-- GRN's own invoice number/date, separate from any file). Multiple rows
-- per PO are expected -- dispatches happen in batches, one invoice each.
-- The actual file bytes live in Storage bucket "po-invoices"; this table
-- is just the metadata + access-control record for those files.
-- ---------------------------------------------------------------------
create table if not exists public.po_invoice_uploads (
  id bigint generated always as identity primary key,
  po_code text not null references public.purchase_orders(po_code) on delete cascade,
  vendor_code text not null,  -- denormalized from the parent PO, for a join-free RLS check
  storage_path text not null unique,  -- "<vendor_code>/<po_code>/<timestamp>-<filename>" in po-invoices
  file_name text not null,
  file_size bigint,
  uploaded_by uuid references auth.users(id) on delete set null,
  uploaded_by_name text,  -- denormalized display name (profiles.vendor_name/email at
                          -- upload time) so the UI can show "who" without an RLS-gated
                          -- join back to profiles (see useInvoiceUploads.js)
  created_at timestamptz not null default now(),
  -- AI match-check result (see the check-invoice-match Edge Function) --
  -- only that function ever writes these (via service_role), never RLS.
  match_status text not null default 'pending'
    check (match_status in ('pending', 'matched', 'mismatch', 'needs_review', 'error')),
  match_summary text,
  match_details jsonb,
  checked_at timestamptz
);

-- po_invoice_uploads already existed before the match-check columns were
-- added, so "create table if not exists" above won't retroactively add
-- them on an already-deployed database -- these do, and are a no-op if
-- already there.
alter table public.po_invoice_uploads add column if not exists match_status text not null default 'pending'
  check (match_status in ('pending', 'matched', 'mismatch', 'needs_review', 'error'));
alter table public.po_invoice_uploads add column if not exists match_summary text;
alter table public.po_invoice_uploads add column if not exists match_details jsonb;
alter table public.po_invoice_uploads add column if not exists checked_at timestamptz;
alter table public.po_invoice_uploads add column if not exists uploaded_by_name text;

-- Payment status, synced from Jarvis (Native payment details). Written
-- ONLY by the sync job via service_role -- deliberately no INSERT/UPDATE
-- policy for authenticated below, same discipline as purchase_orders/grns:
-- this is reported state from a system of record, never something a vendor
-- or an internal user edits by hand in the portal.
--
-- payment_status is NULLABLE on purpose, and null is NOT 'pending'. Null
-- means no payment record has synced for this invoice yet; 'pending' is a
-- positive statement from Jarvis that it's unpaid. The UI keeps those
-- visually distinct (see paymentStatusLabel() in frontend/src/format.js) so
-- an invoice the sync has never seen is never shown as a confirmed unpaid
-- one. Every row is null until the sync exists, which is exactly how the
-- dashboard already read before this column was added.
alter table public.po_invoice_uploads add column if not exists payment_status text
  check (payment_status is null or payment_status in ('pending', 'paid'));
alter table public.po_invoice_uploads add column if not exists payment_date date;
alter table public.po_invoice_uploads add column if not exists payment_ref text;   -- UTR / payment id as Jarvis reports it
alter table public.po_invoice_uploads add column if not exists payment_synced_at timestamptz;

create index if not exists po_invoice_uploads_payment_status_idx
  on public.po_invoice_uploads(payment_status);

-- Superseded by the payment_* columns above. The Jarvis invoice-status
-- sync idea (querying ORACLE_STATUS) was tried and then dropped entirely --
-- nothing writes to these anymore, so dropping is a no-op on data. Kept as
-- explicit drops rather than deleted from this file so an already-applied
-- database converges on re-run, per this schema's idempotency contract.
alter table public.po_invoice_uploads drop column if exists oracle_status;
alter table public.po_invoice_uploads drop column if exists oracle_failure_remarks;
alter table public.po_invoice_uploads drop column if exists oracle_synced_at;

create index if not exists po_invoice_uploads_po_code_idx on public.po_invoice_uploads(po_code);
create index if not exists po_invoice_uploads_vendor_code_idx on public.po_invoice_uploads(vendor_code);

alter table public.po_invoice_uploads enable row level security;

drop policy if exists po_invoice_uploads_select on public.po_invoice_uploads;
create policy po_invoice_uploads_select on public.po_invoice_uploads
  for select
  using (
    public.is_internal_staff()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

-- A vendor can only insert rows tagged with their own vendor_code, and
-- only against a PO that actually belongs to that same vendor_code --
-- so this can't be used to attach an invoice file to someone else's PO.
-- An admin can insert on behalf of any vendor (same PO/vendor_code
-- consistency check still applies -- just not restricted to their own).
drop policy if exists po_invoice_uploads_insert on public.po_invoice_uploads;
create policy po_invoice_uploads_insert on public.po_invoice_uploads
  for insert
  with check (
    exists (
      select 1 from public.purchase_orders po
      where po.po_code = po_invoice_uploads.po_code and po.vendor_code = po_invoice_uploads.vendor_code
    )
    and (
      public.is_internal_staff()
      or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

-- Either the vendor who uploaded it (fixing a mistake) or an admin
-- (cleaning up a wrong/duplicate file) can remove a row.
drop policy if exists po_invoice_uploads_delete on public.po_invoice_uploads;
create policy po_invoice_uploads_delete on public.po_invoice_uploads
  for delete
  using (uploaded_by = auth.uid() or public.is_internal_staff());

-- ---------------------------------------------------------------------
-- Storage bucket "po-invoices" — private (not public), PDF-only; every
-- read/write goes through the RLS policies below, keyed off the path's
-- first folder segment (the vendor_code). Safe to re-run: on conflict
-- updates the size/type limits in place rather than erroring.
-- ---------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'po-invoices', 'po-invoices', false, 15728640,
  array['application/pdf']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists po_invoices_insert on storage.objects;
create policy po_invoices_insert on storage.objects
  for insert
  with check (
    bucket_id = 'po-invoices'
    and (
      public.is_internal_staff()
      or (storage.foldername(name))[1] = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

drop policy if exists po_invoices_select on storage.objects;
create policy po_invoices_select on storage.objects
  for select
  using (
    bucket_id = 'po-invoices'
    and (
      public.is_internal_staff()
      or (storage.foldername(name))[1] = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

drop policy if exists po_invoices_delete on storage.objects;
create policy po_invoices_delete on storage.objects
  for delete
  using (
    bucket_id = 'po-invoices'
    and (
      public.is_internal_staff()
      or (storage.foldername(name))[1] = (select p.vendor_code from public.profiles p where p.id = auth.uid())
    )
  );

-- ---------------------------------------------------------------------
-- mm_rate_card — Mid Mile "Effective Rate Card": one row per
-- Origin|Destination|Truck Size lane, synced from the "Native -
-- Commercials" Google Sheet's "Mid mile commercials" tab (see
-- scripts/sync_mm_rate_card.py — a manual, on-demand GitHub Actions
-- workflow_dispatch, run only when Kalrav says the commercials changed,
-- same convention the source spreadsheet's own weekly-refresh skill
-- already uses for this exact rate card). Read-only for the app --
-- only the sync script (service_role) ever writes it. Powers the
-- Rate Finder page's "cheapest vendor for this lane" lookup.
-- ---------------------------------------------------------------------
create table if not exists public.mm_rate_card (
  lane_key text primary key,     -- "ORIGIN|DESTINATION|TRUCK SIZE", uppercased -- lane
                                  -- matching must be case-insensitive (the source
                                  -- sheet has inconsistent casing; see
                                  -- reference/METHODOLOGY.md in the vendor-adherence
                                  -- skill for the exact prior bug this avoids)
  origin text not null,          -- original casing, for display
  destination text not null,
  truck_size text not null,
  vendor_rates jsonb not null default '{}'::jsonb,  -- {"Lets-Transport": 12000, "Ripplr": null, ...}
  cheapest_vendor text,           -- null if no vendor has quoted this lane at all
  cheapest_price numeric,
  synced_at timestamptz not null default now()
);

alter table public.mm_rate_card enable row level security;

drop policy if exists mm_rate_card_select on public.mm_rate_card;
create policy mm_rate_card_select on public.mm_rate_card
  for select
  using (public.is_internal_staff());  -- no vendor_code scoping -- vendors never see this

-- ---------------------------------------------------------------------
-- vendor_contacts — WhatsApp-reachable phone number per Mid Mile rate-card
-- vendor name (vendor_rates' keys in mm_rate_card above), used by Rate
-- Finder's "Send Intent" button. Deliberately a separate table from the
-- rate-card sync -- a contact number changes independently of pricing,
-- and this is maintained by hand from the admin console, not synced.
-- ---------------------------------------------------------------------
create table if not exists public.vendor_contacts (
  vendor_name text primary key,   -- must match a mm_rate_card.vendor_rates key exactly
  whatsapp_number text not null,  -- E.164, digits only, e.g. "919876543210"
  contact_name text,
  updated_at timestamptz not null default now()
);

alter table public.vendor_contacts enable row level security;

drop policy if exists vendor_contacts_select on public.vendor_contacts;
create policy vendor_contacts_select on public.vendor_contacts
  for select
  using (public.is_internal_staff());

-- No insert/update/delete policy for authenticated on either table above --
-- mm_rate_card is service_role-only (the sync script); vendor_contacts is
-- maintained via the admin-manage-vendor-contacts Edge Function, same
-- service_role-bypasses-RLS pattern as every other admin write path here.

-- ---------------------------------------------------------------------
-- Bootstrap: make yourself the first admin.
--
-- 1. In Supabase Dashboard → Authentication → Users → Add user, create your
--    own login (email + password). Copy the generated User UID.
-- 2. Run this, with your own UID and email substituted in:
--
--   insert into public.profiles (id, role, vendor_name, email)
--   values ('<your-user-uid>', 'admin', 'Kalrav (admin)', '<your-email>');
--
-- After that, your login can see every vendor's data, and only your login
-- (or another row you promote to role='admin') can invoke the admin Edge
-- Functions to create new vendor OR internal-staff (management/operations/
-- finance) logins.
--
-- Internal-staff logins (management/operations/finance) are never created
-- by hand like this -- once you have your first admin login, create those
-- from the "Manage Access" section of the admin console (visible to
-- 'admin' only). That same section's "Edit access" action can also
-- change any existing login's role afterward, INCLUDING promoting one to
-- 'admin' -- Kalrav's explicit call, reversing what used to be a SQL-only
-- restriction here. The safety property that actually matters is still
-- intact: only an existing admin can call that Edge Function
-- (admin-change-access) at all, and it refuses to let a caller change
-- their OWN access level, so a compromised lower-privilege login still
-- can never grant itself admin -- only a legitimate admin choosing to
-- grant it to someone else.
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- One-time backfill: rows synced before vendor_name existed on
-- purchase_orders have it null. New/updated rows get it automatically
-- from here on (the sync script sets it), so this only needs to run
-- once -- safe to leave in / re-run, it's a no-op once every row has it.
-- ---------------------------------------------------------------------
update public.purchase_orders
set vendor_name = 'LEXCRU WATER TECH PVT LTD'
where vendor_code = 'Vendor-156' and vendor_name is null;

-- =======================================================================
-- S&OP section — live replica of the /sop-master Claude Code dashboard's
-- 6 tabs (Inventory Overview, Sales: Plan vs Actual, Day-on-Day Sales,
-- Production Plan, PO Fulfillment, Channel Dispatch Plan), fed by scheduled
-- GitHub Actions Python scripts (scripts/sync_sop_*.py — same
-- service-account-JWT-into-Sheets-API pattern as sync_mm_rate_card.py, no
-- Uniware/Jarvis dependency anywhere) instead of a one-shot agent run.
--
-- Every table below is read-only for the app: is_internal_staff()-gated
-- SELECT only, no INSERT/UPDATE/DELETE policy for authenticated/anon —
-- writes are service_role-only via the sync scripts, same convention as
-- mm_rate_card above. Vendors never see any of this (no vendor_code
-- scoping needed — there's nothing vendor-specific in S&OP data).
--
-- Visibility (frontend concern, not RLS — see AdminApp.vue's canSeeSop):
-- admin / management / operations only, same roles as PO Tracking / SKU
-- Level Data. Finance and vendors excluded.
-- =======================================================================

-- ---------------------------------------------------------------------
-- sop_inventory_channel — Inventory Overview tab, channel x SKU matrix.
-- Synced from WH-Channel-SKU's "Current Inventory" tab by
-- scripts/sync_sop_inventory.py. 10 channel buckets (see
-- INVENTORY_CHANNEL_MAP in that script), 6 SKUs.
-- ---------------------------------------------------------------------
create table if not exists public.sop_inventory_channel (
  id bigserial primary key,
  channel text not null,
  sku text not null,
  qty numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (channel, sku)
);

alter table public.sop_inventory_channel enable row level security;

drop policy if exists sop_inventory_channel_select on public.sop_inventory_channel;
create policy sop_inventory_channel_select on public.sop_inventory_channel
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_inventory_uc_warehouse — Inventory Overview tab, UC APP warehouse
-- view (on-hand from WH-Channel-SKU's "Current Inventory" tab's "UC App -
-- RO" block, per-city; in-transit from Copy Daily Input Anish's "Dispatch
-- Planning" tab's "Intransit Inventory" block). combined is a generated
-- column so the sync script only ever writes on_hand/in_transit.
-- ---------------------------------------------------------------------
create table if not exists public.sop_inventory_uc_warehouse (
  id bigserial primary key,
  warehouse text not null,
  sku text not null,
  on_hand numeric not null default 0,
  in_transit numeric not null default 0,
  combined numeric generated always as (on_hand + in_transit) stored,
  synced_at timestamptz not null default now(),
  unique (warehouse, sku)
);

alter table public.sop_inventory_uc_warehouse enable row level security;

drop policy if exists sop_inventory_uc_warehouse_select on public.sop_inventory_uc_warehouse;
create policy sop_inventory_uc_warehouse_select on public.sop_inventory_uc_warehouse
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_channel_drr_doi — Inventory Overview tab, channel-level DRR/DOI health
-- view (replaces the old UC-warehouse-only on-hand/in-transit/combined
-- tables). DRR = trailing 10-day average from sop_daily_sales's
-- by_sku_uc/by_sku_amazon/by_sku_flipkart/by_sku_mt series; DOI = forward
-- walk against that channel's own "Expected Sale" daily-trackr series,
-- starting from sop_inventory_channel's on-hand. doi_flag mirrors
-- sop_dispatch_plan's projected_doi_flag sentinel for a walk that outruns
-- known data. Wholesale-replaced each run by scripts/sync_sop_inventory.py.
-- ---------------------------------------------------------------------
create table if not exists public.sop_channel_drr_doi (
  id bigserial primary key,
  channel text not null,
  sku text not null,
  drr numeric not null default 0,
  doi numeric,
  doi_flag text,
  synced_at timestamptz not null default now(),
  unique (channel, sku)
);

alter table public.sop_channel_drr_doi enable row level security;

drop policy if exists sop_channel_drr_doi_select on public.sop_channel_drr_doi;
create policy sop_channel_drr_doi_select on public.sop_channel_drr_doi
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_uniware_inventory — raw per-facility x per-SKU on-hand, pulled straight
-- from Uniware's Inventory Snapshot export by scripts/sync_uniware_inventory.py.
--
-- This is the single source of on-hand truth for the whole S&OP section. The
-- derived tables below (sop_inventory_channel, sop_inventory_uc_warehouse,
-- sop_dark_store_inventory) and the dispatch-plan / PO-fulfillment syncs all
-- roll THIS up rather than each parsing the "Current Inventory" sheet with
-- their own slightly different rules, which is what used to let the same
-- warehouse show different stock on different tabs.
--
-- ~28 facilities x 6 SKUs, wholesale-replaced each run. facility is the Uniware
-- code (e.g. 'PB-UC-BLR', 'PB-UC-DEL-SHAHDARA'); sku is our display name.
-- synced_at is surfaced in the UI so a stalled Uniware sync is visible rather
-- than quietly serving yesterday's stock.
-- ---------------------------------------------------------------------
create table if not exists public.sop_uniware_inventory (
  id bigserial primary key,
  facility text not null,
  sku text not null,
  on_hand numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (facility, sku)
);

alter table public.sop_uniware_inventory enable row level security;

drop policy if exists sop_uniware_inventory_select on public.sop_uniware_inventory;
create policy sop_uniware_inventory_select on public.sop_uniware_inventory
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_dark_store_inventory — new "UC App + PLS" tab's "On hand Inventory"
-- view, individual dark-store rows shown below the 5 warehouses (city
-- grouping: DTDC Bangalore/Gurgaon/Kolkata, SFX Mumbai/Hyderabad). Each
-- DTDC/SFX bucket in "Current Inventory" is actually an aggregate label
-- over multiple individual dark stores (21 as of 2026-09-16, confirmed
-- live -- corrects an earlier wrong assumption that only city-aggregated
-- totals existed). Rolled up from sop_uniware_inventory by
-- build_uniware_on_hand() in sync_sop_inventory.py; DARK_STORE_FACILITIES in
-- sop_common.py is the store -> city bucket mapping.
-- ---------------------------------------------------------------------
create table if not exists public.sop_dark_store_inventory (
  id bigserial primary key,
  city text not null,           -- the DTDC/SFX bucket this store belongs to
  store text not null,          -- individual facility code, e.g. 'PB-UC-BLR-NERALURU'
  sku text not null,
  on_hand numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (store, sku)
);

alter table public.sop_dark_store_inventory enable row level security;

drop policy if exists sop_dark_store_inventory_select on public.sop_dark_store_inventory;
create policy sop_dark_store_inventory_select on public.sop_dark_store_inventory
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_facility_drr_doi — "UC App + PLS" tab's warehouse AND dark-store
-- DRR/DOI health view. facility_type 'WAREHOUSE' rows use a 10-day DRR
-- lookback; 'DARK_STORE' rows use 15 days (per Anish) and only exist for
-- the 19 of 21 dark stores that have their own "UC sales trackr" block
-- (2 don't -- see DARK_STORE_TITLE_COLS in sync_sop_inventory.py). DRR =
-- trailing N-day average from "UC sales trackr"'s per-facility Actual
-- Sales blocks (parse_uc_sales_trackr_facility_block in sop_common.py);
-- DOI = simple on_hand/DRR ratio (not a forward-series walk, unlike
-- sop_channel_drr_doi above).
-- ---------------------------------------------------------------------
create table if not exists public.sop_facility_drr_doi (
  id bigserial primary key,
  facility text not null,
  facility_type text not null,  -- 'WAREHOUSE' | 'DARK_STORE'
  sku text not null,
  drr numeric not null default 0,
  doi numeric,
  on_hand numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (facility, sku)
);

alter table public.sop_facility_drr_doi enable row level security;

drop policy if exists sop_facility_drr_doi_select on public.sop_facility_drr_doi;
create policy sop_facility_drr_doi_select on public.sop_facility_drr_doi
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_sales_plan_actual — Sales: Plan vs Actual tab, current month,
-- channel x SKU. Synced from WH-Channel-SKU's "Dashboard" tab (projection,
-- derived from Sale plan x Channel Split) and "actual sales" tab's Q:S
-- block (actuals) by scripts/sync_sop_sales.py.
--
-- 'Others' channel rows always have projection = 0 (no Dashboard-tab
-- counterpart) -- the frontend renders this as a footnote, never silently
-- merges it into MT or drops it.
-- ---------------------------------------------------------------------
create table if not exists public.sop_sales_plan_actual (
  id bigserial primary key,
  month_start date not null,
  channel text not null,
  sku text not null,
  projection numeric not null default 0,
  actual numeric not null default 0,
  gap numeric generated always as (actual - projection) stored,
  synced_at timestamptz not null default now(),
  unique (month_start, channel, sku)
);

alter table public.sop_sales_plan_actual enable row level security;

drop policy if exists sop_sales_plan_actual_select on public.sop_sales_plan_actual;
create policy sop_sales_plan_actual_select on public.sop_sales_plan_actual
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_daily_sales — Day-on-Day Sales tab. One row per (date, series, dim).
-- `series` selects which of the 6 daily blocks in the "actual sales" tab
-- the row came from; `dim` is a SKU code for every series except
-- 'by_channel', where it's a channel name.
-- ---------------------------------------------------------------------
create table if not exists public.sop_daily_sales (
  id bigserial primary key,
  sale_date date not null,
  series text not null,   -- 'by_sku' | 'by_channel' | 'by_sku_uc' | 'by_sku_amazon' |
                           -- 'by_sku_flipkart' | 'by_sku_mt'
  dim text not null,
  qty numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (sale_date, series, dim)
);

alter table public.sop_daily_sales enable row level security;

drop policy if exists sop_daily_sales_select on public.sop_daily_sales;
create policy sop_daily_sales_select on public.sop_daily_sales
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- sop_production_daily — Production Plan tab, live view. One row per
-- (date, sku, facility). Only 'COMBINED' ever has a non-null planned_qty
-- -- no per-facility split exists for PLANNED production in the source
-- sheet (a real limitation, not a bug -- see production_plan_snapshots
-- below for how the portal actually solves the "planned gets silently
-- overwritten by actual" problem instead of just reading this table's
-- live planned_qty for past dates).
-- ---------------------------------------------------------------------
create table if not exists public.sop_production_daily (
  id bigserial primary key,
  prod_date date not null,
  sku text not null,
  facility text not null,   -- 'COMBINED' | 'RONCH' | 'AMBER'
  planned_qty numeric,
  actual_qty numeric,
  synced_at timestamptz not null default now(),
  unique (prod_date, sku, facility)
);

alter table public.sop_production_daily enable row level security;

drop policy if exists sop_production_daily_select on public.sop_production_daily;
create policy sop_production_daily_select on public.sop_production_daily
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- production_plan_snapshots — frozen daily snapshot of planned production,
-- captured once a day (~09:55 AM IST, before that day's own Actual
-- Production cell can flip from a placeholder plan value to the true
-- actual -- a documented, real sheet behavior, not a bug we're working
-- around defensively for no reason) by
-- scripts/sync_sop_production_snapshot.py. Write-once: the sync script
-- always upserts with ON CONFLICT DO NOTHING, so a captured snapshot is
-- frozen forever even on an accidental re-run for the same date.
-- ---------------------------------------------------------------------
create table if not exists public.production_plan_snapshots (
  id bigserial primary key,
  snapshot_date date not null,
  sku text not null,
  facility text not null,   -- 'COMBINED' | 'RONCH' | 'AMBER'
  planned_qty numeric not null,
  captured_at timestamptz not null default now(),
  unique (snapshot_date, sku, facility)
);

alter table public.production_plan_snapshots enable row level security;

drop policy if exists production_plan_snapshots_select on public.production_plan_snapshots;
create policy production_plan_snapshots_select on public.production_plan_snapshots
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- PO Fulfillment + Channel Dispatch Plan tabs. Both are wholesale-
-- replaced per run_date by their sync scripts (delete then insert, not
-- upsert) -- unlike the fixed-shape tables above, these tables' row
-- COUNT varies run to run (PO volume, number of RESCHEDULE/PARTIAL
-- groups, etc.), so upsert's stable-key assumption doesn't hold.
-- ---------------------------------------------------------------------

-- sop_po_fulfillment_daily — date-wise PO fulfillment status, one row per
-- individual PO line per simulated day within the rolling 10-day window.
create table if not exists public.sop_po_fulfillment_daily (
  id bigserial primary key,
  run_date date not null,
  sim_date date not null,
  po_number text,
  warehouse text not null,
  channel text not null,       -- includes 'Amazon (Primarc)', kept distinct from 'Amazon'
  sku text not null,
  po_qty numeric not null,
  status text not null,        -- CONFIRMED | FULFILL | TRANSIT-FULFILL | PARTIAL/NEEDS IN-TRANSIT | RESCHEDULE
  detail text,
  synced_at timestamptz not null default now()
);
create index if not exists idx_sop_po_fulfillment_run on public.sop_po_fulfillment_daily(run_date);

alter table public.sop_po_fulfillment_daily enable row level security;
drop policy if exists sop_po_fulfillment_daily_select on public.sop_po_fulfillment_daily;
create policy sop_po_fulfillment_daily_select on public.sop_po_fulfillment_daily
  for select
  using (public.is_internal_staff());

-- sop_po_action_items — RESCHEDULE/PARTIAL rows grouped by SKU across the whole window.
create table if not exists public.sop_po_action_items (
  id bigserial primary key,
  run_date date not null,
  bucket text not null,        -- RESCHEDULE | PARTIAL
  sku text not null,
  total_qty numeric not null,
  note text,
  synced_at timestamptz not null default now()
);

alter table public.sop_po_action_items enable row level security;
drop policy if exists sop_po_action_items_select on public.sop_po_action_items;
create policy sop_po_action_items_select on public.sop_po_action_items
  for select
  using (public.is_internal_staff());

-- sop_po_shortfall_rca — production-shortfall RCA per SKU, trailing 14 days (today excluded),
-- against production_plan_snapshots. outcome stays 3-way distinguishable -- never collapse
-- NO_SHORTFALL_LIKELY_PO_VOLUME and UNAVAILABLE into one meaning.
create table if not exists public.sop_po_shortfall_rca (
  id bigserial primary key,
  run_date date not null,
  sku text not null,
  outcome text not null,       -- SHORTFALL_DATES_FOUND | NO_SHORTFALL_LIKELY_PO_VOLUME | UNAVAILABLE
  detail_dates jsonb,
  note text,
  synced_at timestamptz not null default now()
);

alter table public.sop_po_shortfall_rca enable row level security;
drop policy if exists sop_po_shortfall_rca_select on public.sop_po_shortfall_rca;
create policy sop_po_shortfall_rca_select on public.sop_po_shortfall_rca
  for select
  using (public.is_internal_staff());

-- sop_dispatch_plan — one row per (run_date, view_key, scope_type, scope, sku, doi_target).
-- scope_type='WAREHOUSE' rows are computed FIRST by the sync script and independently clamped;
-- scope_type='CHANNEL' scope='UC App + PLS' rows are the SUM of those 5 already-clamped warehouse
-- rows, never a separately-computed pooled figure -- see sync_sop_dispatch_plan.py's docstring.
create table if not exists public.sop_dispatch_plan (
  id bigserial primary key,
  run_date date not null,
  view_key text not null,           -- '+7' | '+15' | '+21' | '+30' | '+45' | '+60' | '+90' | 'PINNED'
  scope_type text not null,         -- CHANNEL | WAREHOUSE
  scope text not null,
  sku text not null,
  doi_target int not null,          -- 30 | 15 | 7 | 0
  on_hand numeric,
  po_out numeric,
  sales_expected numeric,
  projected_closing numeric,
  target_closing numeric,
  required_dispatch numeric,
  status text,
  projected_doi numeric,             -- null when projected_doi_flag is set
  projected_doi_flag text,           -- null | '>60' (walk didn't exhaust within 60 days)
  synced_at timestamptz not null default now()
);
create index if not exists idx_sop_dispatch_plan_run_view on public.sop_dispatch_plan(run_date, view_key);

alter table public.sop_dispatch_plan enable row level security;
drop policy if exists sop_dispatch_plan_select on public.sop_dispatch_plan;
create policy sop_dispatch_plan_select on public.sop_dispatch_plan
  for select
  using (public.is_internal_staff());

-- sop_dispatch_production_check — one row per (run_date, view_key, sku): planned production vs
-- total required dispatch over the production lead-time window.
create table if not exists public.sop_dispatch_production_check (
  id bigserial primary key,
  run_date date not null,
  view_key text not null,
  doi_target int,                    -- 30 | 15 | 7 | 0 -- `required` is computed per DOI target
  sku text not null,
  production_planned numeric,
  required numeric,
  gap numeric,
  status text,                       -- SHORTFALL | ON TRACK | N/A
  synced_at timestamptz not null default now()
);
-- Added 2026-09-16: the sync always wrote one row per (view, DOI target, SKU) but had no column to
-- say WHICH target, so the portal couldn't filter and stacked all four sets into one table.
alter table public.sop_dispatch_production_check add column if not exists doi_target int;

alter table public.sop_dispatch_production_check enable row level security;
drop policy if exists sop_dispatch_production_check_select on public.sop_dispatch_production_check;
create policy sop_dispatch_production_check_select on public.sop_dispatch_production_check
  for select
  using (public.is_internal_staff());

-- sop_dispatch_pinned_date — the Channel Dispatch Plan's pinned target date(s) (the business can
-- have more than one active at once -- e.g. 24-Sep-2026 and 30-Sep-2026 coexisted starting
-- 2026-09-15 -- so this is a normal multi-row table, not a singleton). Updated manually via SQL
-- when a date needs to move or a new one is added -- no write policy for authenticated/anon at
-- all, not even the usual service-role-only pattern's implicit "the sync script could write this
-- too": the sync script only ever READS it.
create table if not exists public.sop_dispatch_pinned_date (
  id bigserial primary key,
  pinned_date date not null,
  updated_at timestamptz not null default now()
);
-- Migrates a pre-existing singleton-era table (id bigserial primary key CHECK (id = 1)) to the
-- current multi-row shape -- safe/idempotent to re-run: a no-op once already migrated.
alter table public.sop_dispatch_pinned_date drop constraint if exists sop_dispatch_pinned_date_id_check;
create unique index if not exists sop_dispatch_pinned_date_pinned_date_idx on public.sop_dispatch_pinned_date(pinned_date);

alter table public.sop_dispatch_pinned_date enable row level security;
drop policy if exists sop_dispatch_pinned_date_select on public.sop_dispatch_pinned_date;
create policy sop_dispatch_pinned_date_select on public.sop_dispatch_pinned_date
  for select
  using (public.is_internal_staff());

-- ---------------------------------------------------------------------
-- Bootstrap/maintain the pinned dispatch dates (required once -- the sync script simply skips any
-- pinned date not present here). Add a new date, or re-run this whole block after the business
-- moves/adds one -- every statement is idempotent (on_conflict on the unique pinned_date index
-- above is a no-op for a date already present). To retire a pinned date once it's passed and no
-- longer needed, delete its row manually: delete from public.sop_dispatch_pinned_date where
-- pinned_date = '...';
insert into public.sop_dispatch_pinned_date (pinned_date) values ('2026-09-24')
  on conflict (pinned_date) do nothing;
insert into public.sop_dispatch_pinned_date (pinned_date) values ('2026-09-30')
  on conflict (pinned_date) do nothing;
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- Daily Dispatch Planner (S&OP tab 8) — output of the first-mile dispatch
-- engine, scripts/vendor/first_mile_dispatch.js, which is the SAME engine
-- the /first-mile-dispatch-decision skill runs. Verified 2026-09-17 to
-- produce byte-identical dispatch plans to the skill on identical input;
-- that parity is the whole point of vendoring rather than re-implementing.
--
-- Every table here is keyed (run_date, scenario) and wholesale-replaced per
-- run_date. `scenario` is the toggle the tab exposes:
--   'EXCLUDE_TODAY' — plan from finished goods on the floor right now
--   'INCLUDE_TODAY' — plan assuming today's production run also lands
-- The skill asks this as a question on every run (the FG snapshot carries no
-- timestamp, so it can't be inferred). The portal can't ask, so it computes
-- both and lets you flip between them.
-- ---------------------------------------------------------------------
create table if not exists public.sop_first_mile_plan (
  id bigserial primary key,
  run_date date not null,
  scenario text not null,          -- EXCLUDE_TODAY | INCLUDE_TODAY
  dispatch_date date not null,
  facility text not null,          -- RONCH | AMBER
  warehouse text not null,
  sku text not null,
  qty numeric not null,
  truck_total numeric not null,    -- whole-truck total, repeated on each SKU line of that truck
  eta date not null,
  reason text not null,            -- EMERGENCY | PILE-UP
  tier int,                        -- 0 = a committed PO goes unserved, 1-4 = DOI rungs 7/15/30/45,
                                    -- 5 = pile-gap toward the target split, 6 = nothing needed.
                                    -- Kept because reason flattens all seven into two words.
  synced_at timestamptz not null default now()
);
create index if not exists idx_sop_first_mile_plan_run
  on public.sop_first_mile_plan (run_date, scenario);

-- Plant-side picture: what's on the floor at each plant and what was made today.
-- fg_qty is dispatchable; hold_qty is real stock that is deliberately NOT dispatchable (QC /
-- quarantine / allocation) and is shown separately so it can't be mistaken for available supply.
-- production_raw is the sheet figure; production_yielded applies the engine's 0.9 factor.
create table if not exists public.sop_first_mile_plant (
  id bigserial primary key,
  run_date date not null,
  scenario text not null,
  facility text not null,
  sku text not null,
  fg_qty numeric not null default 0,
  hold_qty numeric not null default 0,
  production_raw numeric not null default 0,
  production_yielded numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (run_date, scenario, facility, sku)
);

create table if not exists public.sop_first_mile_fill_rate (
  id bigserial primary key,
  run_date date not null,
  scenario text not null,
  sku text not null,
  ordered numeric not null default 0,
  served numeric not null default 0,
  short numeric not null default 0,
  fill_pct numeric,
  hard_deficit numeric not null default 0,      -- short because supply never existed
  dispatch_fixable numeric not null default 0,  -- short that better routing could still recover
  synced_at timestamptz not null default now(),
  unique (run_date, scenario, sku)
);

create table if not exists public.sop_first_mile_facility_util (
  id bigserial primary key,
  run_date date not null,
  scenario text not null,
  facility text not null,
  opening_fg numeric not null default 0,
  production numeric not null default 0,
  dispatched numeric not null default 0,
  trucks int not null default 0,
  residual numeric not null default 0,
  synced_at timestamptz not null default now(),
  unique (run_date, scenario, facility)
);

alter table public.sop_first_mile_plan enable row level security;
alter table public.sop_first_mile_plant enable row level security;
alter table public.sop_first_mile_fill_rate enable row level security;
alter table public.sop_first_mile_facility_util enable row level security;

drop policy if exists sop_first_mile_plan_select on public.sop_first_mile_plan;
create policy sop_first_mile_plan_select on public.sop_first_mile_plan
  for select using (public.is_internal_staff());
drop policy if exists sop_first_mile_plant_select on public.sop_first_mile_plant;
create policy sop_first_mile_plant_select on public.sop_first_mile_plant
  for select using (public.is_internal_staff());
drop policy if exists sop_first_mile_fill_rate_select on public.sop_first_mile_fill_rate;
create policy sop_first_mile_fill_rate_select on public.sop_first_mile_fill_rate
  for select using (public.is_internal_staff());
drop policy if exists sop_first_mile_facility_util_select on public.sop_first_mile_facility_util;
create policy sop_first_mile_facility_util_select on public.sop_first_mile_facility_util
  for select using (public.is_internal_staff());
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- sop_first_mile_missed_po — the individual channel POs the dispatch plan
-- leaves short, replacing the per-SKU fill-rate table on the tab. An
-- aggregate tells you M3 Pro is 1,752 short; this tells you which order to
-- ring someone about.
--
-- status is derived from this plan's own simulation, NOT mirrored from
-- sop_po_fulfillment_daily: 'PARTIAL' when the trucks cover some of the
-- order, 'RESCHEDULE' when they cover none of it. The two tabs can disagree,
-- and that's correct -- PO Fulfillment simulates warehouse-side stock over a
-- 10-day window; this simulates plant->warehouse trucks over 15 days and
-- answers "given this plan, what still breaks".
--
-- ATTRIBUTION CAVEAT: the engine serves demand per (date, warehouse, SKU),
-- and several POs routinely share one bucket. Splitting a bucket shortfall
-- across them is an interpretation, done first-come-first-served in sheet
-- order -- the same order parse_po_fulfillment.js walks. The per-PO short
-- figures sum exactly to the headline short in sop_first_mile_fill_rate,
-- which is the invariant worth checking if this is ever changed.
--
-- po_number comes from Raw Data Sheet col 38 and so_number from col 37 --
-- note those columns' HEADERS are swapped relative to their data, so trust
-- the indices, not the labels.
-- ---------------------------------------------------------------------
create table if not exists public.sop_first_mile_missed_po (
  id bigserial primary key,
  run_date date not null,
  scenario text not null,          -- EXCLUDE_TODAY | INCLUDE_TODAY
  po_date date not null,
  po_number text,                  -- blank on ~19% of rows (no PO raised yet); renders as "–"
  so_number text,
  warehouse text not null,
  channel text not null,
  sku text not null,
  ordered numeric not null,
  served numeric not null,
  short numeric not null,
  status text not null,            -- PARTIAL | RESCHEDULE
  synced_at timestamptz not null default now()
);
create index if not exists idx_sop_first_mile_missed_po_run
  on public.sop_first_mile_missed_po (run_date, scenario);

alter table public.sop_first_mile_missed_po enable row level security;
drop policy if exists sop_first_mile_missed_po_select on public.sop_first_mile_missed_po;
create policy sop_first_mile_missed_po_select on public.sop_first_mile_missed_po
  for select using (public.is_internal_staff());
-- ---------------------------------------------------------------------

-- =======================================================================
-- Last Mile Tracking -- warehouse-to-customer delivery visibility, the
-- outbound counterpart to Dispatch Planning's inbound (vendor-to-UC)
-- shipment tracking. Modelled on a working operational report
-- ("Shipment Watch") that already covers 5 LSPs (Blue Dart, Delhivery,
-- DTDC, Holisol, Shadowfax) across Native's D2C/UC-App channels.
--
-- Written by two scripts (daily Uniware pull + hourly courier re-poll --
-- see each script for its own pipeline):
--   scripts/sync_last_mile_daily.py  -- pulls Sale Orders from Uniware,
--     collapses order-items to shipments, writes last_mile_watchlist
--   scripts/sync_last_mile_hourly.py -- re-polls each open shipment's
--     courier, updates last_mile_poll_state, computes the six rollup
--     tables below
--
-- The two tables immediately following (watchlist, poll_state) are
-- DURABLE STATE the pipeline reads AND writes across runs -- the daily
-- job populates watchlist, the hourly job updates poll_state on every
-- run, and both are read back on the next run. This is a deliberate
-- rebuild: a prior version of this pipeline (github.com's own Actions
-- environment has no persistent disk) kept this same state in Claude's
-- artifact database via a read-shard/run/write-shard dance purely
-- because it had nowhere else to put it. This portal already has
-- Postgres, so that whole workaround is gone -- these two tables ARE
-- the durable store now, exactly like every other stateful sync here
-- (see is_settled() in sync_to_supabase.py for the same pattern).
--
-- The six tables AFTER those two are one hourly run's rollup OUTPUT,
-- replaced/upserted wholesale each run -- there is no user-facing write
-- path on those, same discipline as every other sop_* table.
--
-- Internal-staff only (admin/management/operations, same gate as S&OP):
-- this is UC's own delivery operations data, not something a vendor
-- (who only supplies TO Uniware, not to the end customer) has any
-- reason to see.
-- =======================================================================

-- Watchlist: one row per SHIPMENT (an AWB), not per order-item -- a single
-- AWB carries several order items (measured 5.4x on real Native volume),
-- so tracking at item grain would multiply every courier call by five and
-- make "how many shipments are late" impossible to answer. Rebuilt whole
-- by sync_last_mile_daily.py's Sale Orders pull -- upserted on (awb), so
-- a shipment already on file keeps its poll_state history across rebuilds.
--
-- cohort is bookkeeping for the hourly job's own poll scheduling, not
-- shown directly in the UI: 'live' (dispatched recently, poll normally),
-- 'backlog' (dispatched a while ago, still open), 'no_dispatch_date'
-- (Uniware has no dispatch date yet), 'closed' (delivered/cancelled --
-- excluded from polling but kept on file for the run's coverage counts).
create table if not exists public.last_mile_watchlist (
  awb text primary key,
  courier_code text not null,
  adapter_id text not null,       -- bluedart | delhivery | dtdc | shadowfax | holisol | unmapped
  sale_order_codes text[] not null default '{}',
  sale_order_item_codes text[] not null default '{}',
  item_count int not null default 0,
  channel text,
  payment_type text,              -- COD | Prepaid | '' when the export predates the column
  facility_code text,
  city text,
  pincode text,
  shipping_provider text,
  created_at_uniware timestamptz,
  dispatch_date date,
  delivery_time timestamptz,
  item_status text,               -- Sale Order Item Status, e.g. DISPATCHED | DELIVERED | CANCELLED
  uniware_tracking_status text,   -- Uniware's own 89-value LSP-state enum
  uniware_courier_status text,    -- the raw per-carrier string behind it
  package_status_code text,
  promised_date date,
  promise_days int,
  promise_source text,            -- RULES | ASSUMED -- see awb_tracker/watchlist.py's SLA lookup
  promise_slacode text,
  days_since_dispatch numeric,    -- calendar days since dispatch_date, as of the daily pull
  cohort text,                    -- live | backlog | no_dispatch_date | closed
  needs_lsp_poll boolean not null default true,
  awb_pattern_ok boolean,
  last_pulled_at timestamptz not null default now()
);
-- last_mile_watchlist already existed before days_since_dispatch was added
-- (found missing 2026-09-18 when the hourly job read every shipment back
-- with this field silently None, since Shipment.days_since_dispatch had no
-- matching column) -- "create table if not exists" above won't
-- retroactively add it on an already-deployed database; this does, and is
-- a no-op if already there.
alter table public.last_mile_watchlist add column if not exists days_since_dispatch numeric;

create index if not exists idx_last_mile_watchlist_cohort on public.last_mile_watchlist (cohort);
create index if not exists idx_last_mile_watchlist_needs_poll on public.last_mile_watchlist (needs_lsp_poll) where needs_lsp_poll;

alter table public.last_mile_watchlist enable row level security;
drop policy if exists last_mile_watchlist_select on public.last_mile_watchlist;
create policy last_mile_watchlist_select on public.last_mile_watchlist
  for select using (public.is_internal_staff());

-- Poll state: next_poll_at / terminal / confirmed per AWB -- the tiering
-- memory that makes the hourly job cheap. Lose this and every AWB gets
-- polled every hour forever; keep it and a shipment already confirmed
-- delivered, or not yet due for its next check, is skipped outright.
-- Written only by sync_last_mile_hourly.py.
-- next_poll_at is NULLABLE ON PURPOSE: PollState.record() (tiering.py) sets
-- it to None -- not a placeholder time -- for a return "settled on sight"
-- or a delivery that has just received its confirmation re-poll, meaning
-- "never poll this again," not "poll again now." A not-null default of
-- now() would be actively wrong here: it would schedule an immediate
-- re-poll for exactly the shipments the tiering logic just decided are
-- done. Found live 2026-09-18: the first real hourly run failed this
-- constraint on its very last write, after every rollup table (run,
-- coverage, dq_summary, lsp_perf, worst_lanes, alerts) had already
-- written successfully.
create table if not exists public.last_mile_poll_state (
  awb text primary key references public.last_mile_watchlist(awb) on delete cascade,
  next_poll_at timestamptz,
  terminal boolean not null default false,     -- delivered/RTO/lost -- never re-poll
  confirmed boolean not null default false,    -- an LSP call actually returned a result
  last_status text,
  last_polled_at timestamptz,
  poll_count int not null default 0,
  consecutive_failures int not null default 0,
  updated_at timestamptz not null default now()
);
-- last_mile_poll_state already existed with next_poll_at declared NOT NULL
-- -- "create table if not exists" above won't retroactively relax that on
-- an already-deployed database; this does, and is a no-op if already
-- relaxed.
alter table public.last_mile_poll_state alter column next_poll_at drop not null;
alter table public.last_mile_poll_state alter column next_poll_at drop default;

create index if not exists idx_last_mile_poll_state_due
  on public.last_mile_poll_state (next_poll_at) where not terminal;

alter table public.last_mile_poll_state enable row level security;
drop policy if exists last_mile_poll_state_select on public.last_mile_poll_state;
create policy last_mile_poll_state_select on public.last_mile_poll_state
  for select using (public.is_internal_staff());

-- One row per sync run -- run metadata, scope, and the headline counts
-- the page's KPI tiles read. The frontend always wants the latest, so it
-- queries "order by generated_at desc limit 1" rather than this table
-- needing a separate "is this the current run" flag.
create table if not exists public.last_mile_run (
  run_id text primary key,
  generated_at timestamptz not null,
  window_days int not null,
  health text not null,             -- ok | degraded | error -- see sync_last_mile.py
  last_daily_run_at timestamptz,
  last_hourly_run_at timestamptz,
  alerts_total int not null default 0,
  queue_rescue int not null default 0,
  queue_closed_failure int not null default 0,
  queue_data_quality int not null default 0,
  open_shipments int not null default 0,
  live_tracked int not null default 0,
  scope_channels text[],
  scope_note text,
  synced_at timestamptz not null default now()
);
create index if not exists idx_last_mile_run_generated_at on public.last_mile_run (generated_at desc);

alter table public.last_mile_run enable row level security;
drop policy if exists last_mile_run_select on public.last_mile_run;
create policy last_mile_run_select on public.last_mile_run
  for select using (public.is_internal_staff());

-- Coverage: how many shipments are actually being tracked vs excluded,
-- and why. Answers "is this dashboard seeing everything it should"
-- before anyone trusts the alerts below it.
create table if not exists public.last_mile_coverage (
  run_id text primary key references public.last_mile_run(run_id) on delete cascade,
  shipments_total int not null default 0,
  open_total int not null default 0,
  carrier_assigned int not null default 0,
  excluded_by_scope int not null default 0,
  excluded_by_adapter jsonb,        -- {adapter: count}
  excluded_reasons jsonb,           -- {reason: count}
  not_trackable_by_design int not null default 0,
  no_adapter_rule int not null default 0,
  synced_at timestamptz not null default now()
);

alter table public.last_mile_coverage enable row level security;
drop policy if exists last_mile_coverage_select on public.last_mile_coverage;
create policy last_mile_coverage_select on public.last_mile_coverage
  for select using (public.is_internal_staff());

-- Data quality: upstream problems in the SOURCE data (unmapped statuses,
-- unrecognised couriers, malformed or missing AWBs) -- distinct from
-- delivery performance. A high number here means the numbers below it
-- are not yet trustworthy, not that the couriers are doing badly.
create table if not exists public.last_mile_dq_summary (
  run_id text primary key references public.last_mile_run(run_id) on delete cascade,
  unmapped_status_distinct int not null default 0,
  unmapped_status_occurrences int not null default 0,
  unmapped_status_top jsonb,        -- [{value, count}, ...]
  unmapped_courier_distinct int not null default 0,
  unmapped_courier_occurrences int not null default 0,
  unmapped_courier_top jsonb,
  awb_pattern_distinct int not null default 0,
  awb_pattern_occurrences int not null default 0,
  awb_pattern_top jsonb,
  missing_awb_distinct int not null default 0,
  missing_awb_occurrences int not null default 0,
  missing_awb_top jsonb,
  synced_at timestamptz not null default now()
);

alter table public.last_mile_dq_summary enable row level security;
drop policy if exists last_mile_dq_summary_select on public.last_mile_dq_summary;
create policy last_mile_dq_summary_select on public.last_mile_dq_summary
  for select using (public.is_internal_staff());

-- Carrier performance: one row per LSP per run, over the trailing
-- window_days. courier_codes is the (sometimes many) Uniware courier
-- codes that roll up into this one LSP -- kept for drill-down, not
-- shown as a headline number.
create table if not exists public.last_mile_lsp_perf (
  id bigserial primary key,
  run_id text not null references public.last_mile_run(run_id) on delete cascade,
  lsp text not null,
  courier_codes text[],
  delivered int not null default 0,
  on_time int not null default 0,
  late int not null default 0,
  on_time_pct numeric,
  excluded int not null default 0,
  synced_at timestamptz not null default now(),
  unique (run_id, lsp)
);
create index if not exists idx_last_mile_lsp_perf_run on public.last_mile_lsp_perf (run_id);

alter table public.last_mile_lsp_perf enable row level security;
drop policy if exists last_mile_lsp_perf_select on public.last_mile_lsp_perf;
create policy last_mile_lsp_perf_select on public.last_mile_lsp_perf
  for select using (public.is_internal_staff());

-- Worst lanes: one row per (LSP x city) lane whose GRADED volume clears
-- the sync's minimum, worst on-time% first -- where Operations should
-- look first, not every lane in the network.
--
-- Grain is LSP x CITY, not pincode: performance.worst_lanes() aggregates
-- at 'lsp_city' because a single pincode rarely carries enough graded
-- volume to say anything defensible about a carrier. There is deliberately
-- no pincode or facility column here -- they do not exist at this grain,
-- and columns that are always null invite false confidence.
--
-- "graded" is the denominator that matters: shipments with a real promise
-- date that have actually resolved on-time or late. It is NOT the raw
-- shipment count -- anything still in flight, or carrying only an ASSUMED
-- promise, cannot be graded and is excluded (see excluded_assumed_promise).
--
-- ONE-TIME MIGRATION, conditional, not a plain drop: an earlier version of
-- this table shipped with a pincode/facility_code/volume/delivered/
-- avg_days_late shape that turned out not to match what
-- performance.worst_lanes() actually returns (see sync_last_mile_hourly.py's
-- fix, 2026-09-18). "create table if not exists" is a no-op against an
-- already-deployed table, so changing the column list below would silently
-- never apply to a live database without this.
--
-- The drop only fires if the OLD `pincode` column is still present, which
-- was true only while this table had zero rows on file (no hourly sync had
-- run yet). Once migrated, `pincode` is gone, this check is false forever
-- after, and re-running schema.sql stays a safe no-op for this table --
-- same "safe to re-run anytime" contract as the rest of this file. Do NOT
-- widen this to an unconditional drop; a future re-apply must never wipe
-- real alert/lane data.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'last_mile_worst_lanes' and column_name = 'pincode'
  ) then
    drop table public.last_mile_worst_lanes;
  end if;
end $$;

create table if not exists public.last_mile_worst_lanes (
  id bigserial primary key,
  run_id text not null references public.last_mile_run(run_id) on delete cascade,
  lsp text not null,
  city text,
  graded int not null default 0,        -- on_time + late, the gradeable population
  late int not null default 0,
  on_time_pct numeric,
  avg_transit_days numeric,
  p85_transit_days numeric,             -- the tail, which an average hides
  active int not null default 0,        -- still in flight on this lane
  breached int not null default 0,
  rto_in_flight int not null default 0,
  excluded_assumed_promise int not null default 0,
  synced_at timestamptz not null default now()
);
create index if not exists idx_last_mile_worst_lanes_run on public.last_mile_worst_lanes (run_id);

alter table public.last_mile_worst_lanes enable row level security;
drop policy if exists last_mile_worst_lanes_select on public.last_mile_worst_lanes;
create policy last_mile_worst_lanes_select on public.last_mile_worst_lanes
  for select using (public.is_internal_staff());

-- Alerts: one row per shipment currently flagged. This is the
-- actionable queue -- everything else on the page is context for this
-- table. bucket groups flags by what kind of action is needed:
--   rescue         -- still fixable (stuck, no pickup, breached SLA,
--                     failed delivery attempt) -- Operations should chase
--   closed_failure -- resolved but badly (lost, RTO) -- for reporting,
--                     not action
--   data_quality   -- cannot be judged (AWB not found, no adapter rule)
--                     -- a pipeline gap, not a delivery failure
-- primary_flag is the single most-actionable flag when a shipment trips
-- several (severity order lives in sync_last_mile.py); flags carries the
-- full set for anyone who wants it.
create table if not exists public.last_mile_alerts (
  id bigserial primary key,
  run_id text not null references public.last_mile_run(run_id) on delete cascade,
  awb text not null,
  flags text[] not null default '{}',
  primary_flag text not null,
  bucket text not null check (bucket in ('rescue', 'closed_failure', 'data_quality')),
  severity int,
  lsp text,
  courier_code text,
  facility_code text,
  city text,
  pincode text,
  channel text,
  payment_type text,
  sale_order_codes text[],
  item_count int,
  status text,
  raw_status text,
  status_source text,
  status_at timestamptz,
  last_scan_location text,
  promised_date date,
  promise_source text,
  days_overdue numeric,
  days_since_dispatch numeric,
  hours_since_scan numeric,
  attempts int,
  ndr_reason text,
  notes text,
  synced_at timestamptz not null default now(),
  unique (run_id, awb)
);
create index if not exists idx_last_mile_alerts_run on public.last_mile_alerts (run_id);
create index if not exists idx_last_mile_alerts_bucket on public.last_mile_alerts (run_id, bucket);

-- severity was originally `text`; alerts.Alert.severity is an int (e.g. 80),
-- so a live database created before this line existed still has the wrong
-- type -- same "create table if not exists is a no-op on an existing table"
-- trap as last_mile_worst_lanes above. Unlike that table, this is a single
-- column, so a direct alter is enough rather than a conditional drop -- and
-- it is safe to run unconditionally on every re-apply: converting an
-- already-int column to int via `using severity::int` is a harmless no-op,
-- so this line never needs to be removed once it has taken effect.
alter table public.last_mile_alerts alter column severity type int using severity::int;

alter table public.last_mile_alerts enable row level security;
drop policy if exists last_mile_alerts_select on public.last_mile_alerts;
create policy last_mile_alerts_select on public.last_mile_alerts
  for select using (public.is_internal_staff());
-- ---------------------------------------------------------------------
