-- What each run actually changed, job by job.
--
-- The pull already builds a readable list — "#QU-1351 install date set to
-- 2026-10-22" — and returns it to the page, which then discards it. Keeping
-- it means the history answers "what did last night's run do" rather than
-- only "how many things did it do".

alter table sync_runs add column if not exists changes jsonb;

-- Verify:
select started_at, trigger, jsonb_array_length(coalesce(changes,'[]'::jsonb)) as n
from sync_runs order by started_at desc limit 5;
