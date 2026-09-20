-- Track what's happening to a damaged item.
--
--   to_schedule  — logged, needs a decision (default for new reports)
--   scheduled    — booked in to be fixed
--   fixed        — repaired and back in service
--   discard      — not worth fixing, to be written off
--
-- Existing reports get 'to_schedule' so nothing sits in limbo with no status.

alter table damage_reports add column if not exists repair_status text;

update damage_reports
   set repair_status = 'to_schedule'
 where repair_status is null;

-- Verify:
select repair_status, count(*)
from damage_reports
group by repair_status
order by repair_status;
