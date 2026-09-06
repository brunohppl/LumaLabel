-- Photography time pulled from Monday's PHOTOS column.
--
-- This is the firm deadline the whole day works back from: the property
-- must be styled and shot by then. Stored as free text exactly as Monday
-- reports it, because Monday columns aren't guaranteed to be a time type
-- and a half-parsed deadline is worse than the raw one.

alter table jobs add column if not exists photo_time text;

-- Verify:
select column_name, data_type from information_schema.columns
where table_name = 'jobs' and column_name = 'photo_time';
