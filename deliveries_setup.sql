-- ═══════════════════════════════════════════════════════════════
-- Deliveries module — Programma import & delivery checking
-- Separate department. All tables namespaced delivery_* so they can
-- never collide with the warehouse app's jobs / items tables.
-- Safe to run on the existing database: creates only new tables.
-- ═══════════════════════════════════════════════════════════════

-- One row per imported Programma schedule
create table if not exists delivery_projects (
  id                uuid primary key default gen_random_uuid(),
  created_at        timestamptz default now(),
  name              text not null,          -- what the user calls it
  programma_project text,                   -- name as it appears in the export
  source_filename   text,
  notes             text
);

-- One row per line of the schedule
create table if not exists delivery_lines (
  id               uuid primary key default gen_random_uuid(),
  created_at       timestamptz default now(),
  project_id       uuid not null references delivery_projects(id) on delete cascade,
  section          text,                    -- room, e.g. "Master Bedroom"
  item_label       text,                    -- Programma "Product Description"
  product_name     text,
  brand            text,
  sku              text,
  doc_code         text,
  colour           text,
  finish           text,
  material         text,
  dimensions       text,
  lead_time        text,
  qty_expected     integer default 1,
  qty_received     integer default 0,
  rrp              text,
  programma_status text,                    -- draft/quoting/paid/delivered…
  supplier         text,
  url              text,
  important_info   text,
  notes            text,
  is_service       boolean default false    -- budget/service line, not a delivery
);

-- One row per check-in event (used from the next step onward).
-- An event log rather than a flag, so partial deliveries across
-- several days keep a full audit trail of who received what and when.
create table if not exists delivery_checks (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz default now(),
  project_id  uuid references delivery_projects(id) on delete cascade,
  line_id     uuid references delivery_lines(id) on delete cascade,
  qty         integer default 1,
  checked_by  text,
  condition   text,                         -- ok / damaged
  barcode     text,                         -- scanned code, if any
  photo_url   text,
  note        text
);

create index if not exists delivery_lines_project_idx  on delivery_lines(project_id);
create index if not exists delivery_lines_sku_idx      on delivery_lines(sku);
create index if not exists delivery_checks_line_idx    on delivery_checks(line_id);
create index if not exists delivery_checks_project_idx on delivery_checks(project_id);

alter table delivery_projects enable row level security;
alter table delivery_lines    enable row level security;
alter table delivery_checks   enable row level security;
create policy "Allow all" on delivery_projects for all using (true) with check (true);
create policy "Allow all" on delivery_lines    for all using (true) with check (true);
create policy "Allow all" on delivery_checks   for all using (true) with check (true);
