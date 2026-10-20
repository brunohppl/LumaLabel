-- Where a run had got to when it stopped.
--
-- The row was written at the start and the end only, so a run cut off in
-- between showed "did not finish" and nothing more. This records each
-- phase as it happens, which turns that into "died while fetching the
-- board, after 1,000 items".

alter table sync_runs add column if not exists stage text;

-- Verify:
select started_at, trigger, status, stage from sync_runs order by started_at desc limit 5;
