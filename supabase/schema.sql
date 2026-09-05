-- Lexcru / vendor PO tracker — Supabase schema + Row Level Security
--
-- Run this once in the Supabase SQL Editor (Project → SQL Editor → New query)
-- right after creating the project. Safe to re-run (uses IF NOT EXISTS /
-- CREATE OR REPLACE / DROP POLICY IF EXISTS throughout).
--
-- Data model:
--   profiles          — one row per login (admin or vendor), 1:1 with
--                        auth.users via id. vendor_code is null for admins,
--                        set to e.g. "Vendor-156" for a vendor login — that's
--                        the only thing that scopes what a vendor can see.
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
--   - An admin login (profiles.role = 'admin') can SELECT everything.
--   - Nobody except service_role can INSERT/UPDATE/DELETE anywhere — all
--     writes happen server-side (the Actions pipeline for PO/GRN data, the
--     admin Edge Function for vendor accounts).

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
  role text not null check (role in ('admin', 'vendor')),
  vendor_code text,          -- required for role='vendor', null for admins
  vendor_name text,          -- display name only, e.g. "LEXCRU WATER TECH PVT LTD"
  email text,                -- denormalized copy of auth.users.email, for admin display only
  created_at timestamptz not null default now()
);

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

alter table public.profiles enable row level security;

drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
  for select
  using (id = auth.uid() or public.is_admin());

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

create index if not exists purchase_orders_vendor_code_idx on public.purchase_orders(vendor_code);

alter table public.purchase_orders enable row level security;

drop policy if exists purchase_orders_select on public.purchase_orders;
create policy purchase_orders_select on public.purchase_orders
  for select
  using (
    public.is_admin()
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
    public.is_admin()
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

create index if not exists po_items_po_code_idx on public.po_items(po_code);
create index if not exists po_items_vendor_code_idx on public.po_items(vendor_code);

alter table public.po_items enable row level security;

drop policy if exists po_items_select on public.po_items;
create policy po_items_select on public.po_items
  for select
  using (
    public.is_admin()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

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
    public.is_admin()
    or vendor_code = (select p.vendor_code from public.profiles p where p.id = auth.uid())
  );

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
-- Function to create new vendor logins.
-- ---------------------------------------------------------------------
