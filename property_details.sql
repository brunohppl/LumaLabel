-- Property details pulled from Monday: type, size and style.
--
-- The jobs page and the runsheet tiles already display type and size, but
-- nothing ever wrote them, so the tag never appeared. Style is new.

alter table jobs add column if not exists property_type  text;
alter table jobs add column if not exists property_size  text;
alter table jobs add column if not exists property_style text;

-- Verify:
select column_name, data_type
from information_schema.columns
where table_name = 'jobs'
  and column_name in ('property_type','property_size','property_style')
order by column_name;
