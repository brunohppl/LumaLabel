-- People added through the app, on top of the built-in roster.
--
-- Deliberately additive: the names hard-coded in app.py remain the base
-- list and the fallback, so if this table is missing or unreachable every
-- crew picker still works exactly as it does today. Only people ADDED
-- through the app live here.
--
-- Nothing else references this table. Crews on saved days store member
-- names as plain text, so adding or deactivating someone never changes a
-- day that has already been scheduled.

create table if not exists staff (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  active     boolean not null default true,
  created_at timestamptz not null default now()
);

-- One row per name, so adding the same person twice is harmless
create unique index if not exists staff_name_uidx on staff (lower(name));

-- Verify:
select name, active from staff order by name;
