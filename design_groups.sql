-- Design groups: one row per distinct piece of furniture we own.
--
-- furniture_catalogue holds one entry per item per job, so the same sofa
-- appears many times over. A group gathers those entries under one design,
-- which is what counts, availability and style tags should hang off later.
--
-- Nothing existing changes: group_id is nullable, and an ungrouped entry
-- behaves exactly as it does today.

create table if not exists design_groups (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  type        text,                      -- editable; seeded from the entries
  notes       text,
  owned_count integer,                   -- phase 2
  created_at  timestamptz not null default now()
);

create unique index if not exists design_groups_name_uidx on design_groups (lower(name));

alter table furniture_catalogue add column if not exists group_id uuid references design_groups(id) on delete set null;
create index if not exists furniture_catalogue_group_idx on furniture_catalogue (group_id);

-- New tables have RLS on with no policies, which blocks the app's key.
alter table design_groups enable row level security;
drop policy if exists "design groups select" on design_groups;
drop policy if exists "design groups insert" on design_groups;
drop policy if exists "design groups update" on design_groups;
drop policy if exists "design groups delete" on design_groups;
create policy "design groups select" on design_groups for select using (true);
create policy "design groups insert" on design_groups for insert with check (true);
create policy "design groups update" on design_groups for update using (true) with check (true);
create policy "design groups delete" on design_groups for delete using (true);

-- Verify:
select count(*) as catalogue_entries from furniture_catalogue;
select count(*) as groups from design_groups;
