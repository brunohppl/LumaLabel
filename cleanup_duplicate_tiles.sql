-- ═══════════════════════════════════════════════════════════════
-- One-off cleanup: duplicate runsheet tiles
--
-- Duplicates were created three ways:
--   1. a label upload seeded a tile, then a Monday pull added its own
--      (the pull only recognised its own tiles, by monday_item_id)
--   2. a Monday placeholder was attached to a job that had just been
--      seeded, leaving both
--   3. setting a date with the calendar wiped monday_item_id, so every
--      later pull inserted a fresh tile
--
-- SAFETY — this only removes UNSCHEDULED tiles (no team, no start time).
-- A job legitimately has more than one tile when it's been split across
-- vehicles, and those are always placed, so they are never touched.
-- ═══════════════════════════════════════════════════════════════

-- STEP 1 — look before you delete. Run this on its own first:
select job_id, type, date, count(*) as copies
from job_schedule
where job_id is not null
  and team_id is null
  and start_time is null
group by job_id, type, date
having count(*) > 1
order by copies desc;

-- STEP 2 — remove the extras, keeping the earliest of each set and
-- preferring to keep whichever row carries the Monday link.
with ranked as (
  select id,
         row_number() over (
           partition by job_id, type, date
           order by (monday_item_id is null), created_at
         ) as rn
  from job_schedule
  where job_id is not null
    and team_id is null
    and start_time is null
)
delete from job_schedule
where id in (select id from ranked where rn > 1);

-- STEP 3 — confirm it's clean. Should return no rows.
select job_id, type, date, count(*)
from job_schedule
where job_id is not null and team_id is null and start_time is null
group by job_id, type, date
having count(*) > 1;
