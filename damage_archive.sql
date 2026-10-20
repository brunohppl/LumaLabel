-- Archiving for damage reports.
--
-- Archive, not delete: an archived report keeps every field, its photo and
-- its history. It simply stops cluttering the main list once it has been
-- dealt with. Delete already exists for genuine mistakes.

alter table damage_reports add column if not exists archived boolean not null default false;

-- Verify:
select column_name, data_type, column_default
from information_schema.columns
where table_name = 'damage_reports' and column_name = 'archived';
