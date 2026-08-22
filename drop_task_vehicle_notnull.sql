-- runsheet_tasks.vehicle dates from when every column was a truck. The
-- Warehouse column has no vehicle, and breaks belong to a crew rather than
-- a vehicle — both hit the NOT NULL constraint and failed with 23502.
--
-- The app now sends '' instead of null, so this migration is OPTIONAL —
-- run it for clean data (null rather than empty string), not to fix the bug.

alter table runsheet_tasks alter column vehicle drop not null;

-- Tidy anything already stored as an empty string:
update runsheet_tasks set vehicle = null where vehicle = '';

-- Verify:
select is_nullable from information_schema.columns
where table_name = 'runsheet_tasks' and column_name = 'vehicle';
