-- A record of every Monday pull — pressed or scheduled.
--
-- A row is written when a run STARTS and updated when it finishes. A row
-- that never finished is the evidence of a crash: without it, a failed
-- nightly run looks exactly like a run that never happened.

create table if not exists sync_runs (
  id             uuid primary key default gen_random_uuid(),
  trigger        text not null,              -- 'manual' or 'scheduled'
  status         text not null default 'running',   -- running | ok | error
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  duration_ms    integer,

  dates_updated      integer default 0,
  statuses_updated   integer default 0,
  completed_updated  integer default 0,
  details_updated    integer default 0,
  tiles_created      integer default 0,
  tiles_updated      integer default 0,
  skipped_errors     integer default 0,
  unmatched          integer default 0,
  writes_remaining   integer default 0,      -- >0 means the cap was hit

  error          text
);

create index if not exists sync_runs_started_idx on sync_runs (started_at desc);

-- New tables have RLS on with no policies, which blocks the app's key.
alter table sync_runs enable row level security;
drop policy if exists "sync runs select" on sync_runs;
drop policy if exists "sync runs insert" on sync_runs;
drop policy if exists "sync runs update" on sync_runs;
create policy "sync runs select" on sync_runs for select using (true);
create policy "sync runs insert" on sync_runs for insert with check (true);
create policy "sync runs update" on sync_runs for update using (true) with check (true);

-- Verify:
select count(*) from sync_runs;
