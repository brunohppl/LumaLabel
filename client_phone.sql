-- The client's mobile, pulled from the Monday board's "Client Phone" column.
--
-- Additive only: nullable, nothing reads it yet beyond displaying it, and no
-- existing column is touched. Nothing is sent anywhere — this stores and
-- shows a number so the reminder feature has something to work from later.

alter table jobs add column if not exists client_phone text;

-- Verify:
select column_name, data_type, is_nullable
from information_schema.columns
where table_name = 'jobs' and column_name = 'client_phone';
