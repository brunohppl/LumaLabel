-- Archiving for delivery projects, and the index the cross-project search
-- needs to stay fast.
--
-- Archiving is a flag, not a delete: an archived project keeps every line,
-- received count and check record. It simply drops out of the default list
-- and out of search results.

alter table delivery_projects add column if not exists archived boolean not null default false;

-- Search filters on these three columns across every project at once
create index if not exists delivery_lines_search_idx
  on delivery_lines (project_id);

-- Verify:
select column_name, data_type, column_default
from information_schema.columns
where table_name = 'delivery_projects' and column_name = 'archived';
