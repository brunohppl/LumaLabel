-- LUMA Design Co — Warehouse Database
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New query)

-- Jobs table
create table if not exists jobs (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz default now(),
  job_number   text not null,
  job_ref      text,
  address      text,
  stage_date   text,
  colour       text,
  status       text default 'picking',  -- picking | ready | loaded
  item_count   integer default 0,
  checked_count integer default 0
);

-- Items table
create table if not exists items (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz default now(),
  job_id      uuid references jobs(id) on delete cascade,
  serial      text,
  room        text,
  description text,
  is_extra    boolean default false,
  checked     boolean default false
);

-- Allow public read/write via anon key (for the web app)
alter table jobs  enable row level security;
alter table items enable row level security;

create policy "Allow all" on jobs  for all using (true) with check (true);
create policy "Allow all" on items for all using (true) with check (true);

-- Index for fast job lookups
create index if not exists items_job_id_idx on items(job_id);
create index if not exists jobs_created_at_idx on jobs(created_at desc);

-- ── Migrations added since initial setup ──
-- Safe to run on a fresh database too — these are all no-ops if the
-- columns already exist.
alter table jobs  add column if not exists job_owner          text default '';
alter table jobs  add column if not exists truck              text default '';
alter table jobs  add column if not exists styling_notes      text default '';
alter table jobs  add column if not exists driver_notes       text default '';
alter table jobs  add column if not exists is_transfer        boolean default false;
alter table jobs  add column if not exists transfer_from_job_id uuid references jobs(id) on delete set null;
alter table jobs  add column if not exists truck_eta_text         text default null;
alter table jobs  add column if not exists truck_eta_calculated_at timestamptz default null;
alter table jobs  add column if not exists stylist_eta_text         text default null;
alter table jobs  add column if not exists stylist_eta_calculated_at timestamptz default null;
alter table jobs  add column if not exists runsheet_date          date default null;
alter table jobs  add column if not exists runsheet_type          text default null;
-- If upgrading: any flat runsheet_* columns on jobs from earlier iterations
-- (runsheet_time, runsheet_duration, runsheet_vehicles, runsheet_lead,
--  runsheet_offsiders) are no longer used. Safe to drop or leave in place.

-- Per-job vehicle assignments: vehicle + when + how long.
-- date is the specific day this entry appears on the runsheet grid —
-- independent of jobs.runsheet_date, so one job can appear on multiple
-- days (e.g. load day + install day are always different calendar days).
-- No crew here — crew is a vehicle-level concern stored in vehicle_day_crew.
create table if not exists job_schedule (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz default now(),
  job_id       uuid not null references jobs(id) on delete cascade,
  vehicle      text not null,
  date         date,             -- which day this entry appears on the grid
  type         text,             -- 'install', 'pickup', or 'to_load' — per entry, overrides job.runsheet_type
  start_time   text,             -- "HH:MM", nullable (shows in unscheduled strip)
  duration     integer,          -- minutes, nullable (UI defaults to 60)
  notes        text              -- per-vehicle notes: access info, instructions, etc.
);
create index if not exists job_schedule_job_id_idx on job_schedule(job_id);

-- Crew for a vehicle on a specific day.
-- Set once per vehicle per day from the team row in the runsheet grid.
-- Upserted on (vehicle, date) so editing replaces rather than duplicates.
create table if not exists vehicle_day_crew (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz default now(),
  vehicle      text not null,
  date         date not null,
  lead         text,
  offsiders    text[] default '{}',
  unique(vehicle, date)
);
create index if not exists vdc_date_idx on vehicle_day_crew(date);

-- Freestanding tasks on the runsheet grid — not tied to any job.
-- vehicle is either a specific vehicle name (e.g. "Bruce") or "ALL"
-- which renders the task across every vehicle column simultaneously.
create table if not exists runsheet_tasks (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz default now(),
  vehicle      text not null,   -- vehicle name or "ALL"
  date         date not null,
  title        text not null,
  notes        text,
  start_time   text,            -- "HH:MM", nullable
  duration     integer          -- minutes, nullable (UI defaults to 60)
);
create index if not exists rt_date_idx on runsheet_tasks(date);
alter table runsheet_tasks enable row level security;
create policy "Allow all" on runsheet_tasks for all using (true) with check (true);

-- RLS for the new tables — same "Allow all" pattern as jobs/items/room_notes.
-- Without these, Supabase silently rejects all reads and writes even with
-- a valid service-role key, because RLS is enabled by default on new tables.
alter table job_schedule     enable row level security;
alter table vehicle_day_crew enable row level security;
create policy "Allow all" on job_schedule     for all using (true) with check (true);
create policy "Allow all" on vehicle_day_crew for all using (true) with check (true);

-- If upgrading from before the stylist ETA feature: the old columns
-- were named eta_text / eta_calculated_at and only ever held the
-- driver's ETA. This copies any existing value across to the new
-- truck-prefixed columns before they'd otherwise sit unused. Safe to
-- run on a fresh database too — the old columns won't exist, so these
-- just no-op.
do $$
begin
  if exists (select 1 from information_schema.columns where table_name='jobs' and column_name='eta_text') then
    update jobs set truck_eta_text = eta_text, truck_eta_calculated_at = eta_calculated_at
      where eta_text is not null and truck_eta_text is null;
    alter table jobs drop column eta_text;
    alter table jobs drop column eta_calculated_at;
  end if;
end $$;
alter table items add column if not exists notes              text default '';
alter table items add column if not exists picked             boolean default false;
alter table items add column if not exists photo_url          text default null;
alter table items add column if not exists is_transfer_item   boolean default false;
alter table items add column if not exists not_transferring   boolean default false;
alter table items add column if not exists on_truck           boolean default false;
alter table items add column if not exists bay_location       text default null;
alter table jobs  add column if not exists accessory_tubs     integer default null;
alter table jobs  add column if not exists cushion_bags        integer default null;

-- Additional photos per item — supports multiple photos on the stylist interface.
-- photo_url on items remains as the "primary" photo (first/most recent) for
-- backward compatibility with the driver interface which only shows one photo.
-- item_photos holds all photos including the primary.
create table if not exists item_photos (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz default now(),
  item_id      uuid not null references items(id) on delete cascade,
  url          text not null
);
create index if not exists item_photos_item_id_idx on item_photos(item_id);
alter table item_photos enable row level security;
create policy "Allow all" on item_photos for all using (true) with check (true);

-- Furniture catalogue — one entry per notable item photo.
-- Auto-populated when a stylist takes the first photo of an item during a job.
-- type: category label (e.g. "Sofas", "Chairs") — same vocabulary as driver interface.
-- room_context: the room the item was placed in (e.g. "Living Room", "Master Bedroom").
-- item_id / job_id: traceback to the source job (nullable — manual entries have no source).
-- ON DELETE SET NULL so deleting a job/item doesn't wipe catalogue entries.
create table if not exists furniture_catalogue (
  id                 uuid primary key default gen_random_uuid(),
  created_at         timestamptz default now(),
  type               text not null,
  room_context       text,
  description        text,
  warehouse_location text,
  photo_url          text not null,
  item_id            uuid references items(id) on delete set null,
  job_id             uuid references jobs(id) on delete set null
);
create index if not exists fc_type_idx on furniture_catalogue(type);
create index if not exists fc_room_idx on furniture_catalogue(room_context);
alter table furniture_catalogue enable row level security;
create policy "Allow all" on furniture_catalogue for all using (true) with check (true);

-- Room-level notes parsed from packing-slip bracket text, e.g.
-- "[MOVECHAISEFORSOFATOMEDIA]" -> "Move chaise for sofa to media"
-- Shown on the stylist interface next to the room title; intentionally
-- not surfaced to drivers.
create table if not exists room_notes (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  job_id     uuid references jobs(id) on delete cascade,
  room       text,
  note       text
);

alter table room_notes enable row level security;
create policy "Allow all" on room_notes for all using (true) with check (true);
create index if not exists room_notes_job_id_idx on room_notes(job_id);

-- Damage reports — logged by staff via the /damages page.
create table if not exists damage_reports (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz default now(),
  location     text not null,
  damage_type  text not null,
  furniture    text,
  photo_url    text,
  notes        text
);
alter table damage_reports enable row level security;
create policy "Allow all" on damage_reports for all using (true) with check (true);

-- Property damage support (run manually if damage_reports already exists)
alter table damage_reports add column if not exists report_category text default 'furniture';
alter table damage_reports add column if not exists property_element text;
alter table damage_reports alter column furniture drop not null;

-- Runsheet restructure: category and person fields
alter table job_schedule add column if not exists category text default 'transport';
alter table job_schedule add column if not exists person   text default null;
alter table runsheet_tasks add column if not exists category text default 'warehouse';

-- Lead and team per schedule entry
alter table job_schedule add column if not exists lead text default null;
alter table job_schedule add column if not exists team text[] default null;

-- Day teams — one per column on the runsheet grid
create table if not exists day_teams (
  id         uuid primary key default gen_random_uuid(),
  date       date not null,
  name       text,
  vehicle    text,
  function   text not null default 'transport', -- transport, styling, warehouse
  lead       text,
  members    text[] default '{}',
  colour     text,
  sort_order integer default 0,
  created_at timestamptz default now()
);
alter table day_teams enable row level security;
create policy "Allow all" on day_teams for all using (true) with check (true);
create index if not exists day_teams_date_idx on day_teams(date);

-- Link job_schedule entries to a team
alter table job_schedule add column if not exists team_id uuid references day_teams(id) on delete set null;

-- Team templates
create table if not exists team_templates (
  id       uuid primary key default gen_random_uuid(),
  name     text not null,
  vehicle  text,
  function text not null default 'transport',
  lead     text,
  members  text[] default '{}'
);
alter table team_templates enable row level security;
create policy "Allow all" on team_templates for all using (true) with check (true);
