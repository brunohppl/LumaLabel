-- Every tap of a Navigate button: who, where to, and when.
--
-- The app has no record of when anything actually happens — durations on
-- the runsheet are planned, not measured. This is the first real timestamp
-- it collects. Nothing reads it yet; it accumulates so that in a month
-- there is something real to look at.
--
-- What it is NOT: an arrival. A tap means "setting off", so the gap between
-- leaving for a job and leaving for the warehouse covers travel AND time on
-- site. The eta_minutes column holds the travel estimate so the two can be
-- separated, roughly.

create table if not exists nav_events (
  id           uuid primary key default gen_random_uuid(),
  destination  text not null,              -- 'job' or 'warehouse'
  job_id       uuid references jobs(id) on delete set null,
  vehicle      text,                       -- the truck, where known
  team_id      uuid,                       -- the crew column it came from
  role         text,                       -- 'truck' or 'stylist'
  eta_minutes  integer,                    -- travel estimate at the time
  lat          double precision,
  lng          double precision,
  created_at   timestamptz not null default now()
);

create index if not exists nav_events_created_idx on nav_events (created_at desc);
create index if not exists nav_events_job_idx     on nav_events (job_id);
create index if not exists nav_events_vehicle_idx on nav_events (vehicle, created_at desc);

-- New tables have RLS on with no policies, which blocks the app's key.
alter table nav_events enable row level security;
drop policy if exists "nav events select" on nav_events;
drop policy if exists "nav events insert" on nav_events;
create policy "nav events select" on nav_events for select using (true);
create policy "nav events insert" on nav_events for insert with check (true);

-- Verify:
select count(*) from nav_events;
