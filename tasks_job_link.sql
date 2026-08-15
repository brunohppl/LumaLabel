-- Let a runsheet task belong to a job.
--
-- Nullable on purpose: plenty of warehouse tasks stand alone and shouldn't be
-- forced onto a job. This makes job_id the single reference every piece of
-- scheduled work can share — install tiles, load tiles and tasks alike.
--
-- The column type is copied from job_schedule.job_id rather than assumed, so
-- it matches whatever jobs.id actually is.

do $$
declare
  coltype text;
begin
  select format_type(a.atttypid, a.atttypmod)
    into coltype
  from pg_attribute a
  where a.attrelid = 'job_schedule'::regclass
    and a.attname  = 'job_id'
    and a.attnum   > 0;

  if coltype is null then
    raise exception 'Could not read job_schedule.job_id — check the table name';
  end if;

  execute format(
    'alter table runsheet_tasks add column if not exists job_id %s references jobs(id) on delete set null',
    coltype
  );
end $$;

-- Deleting a job leaves its tasks in place with no link, rather than silently
-- removing scheduled work from the runsheet.

create index if not exists runsheet_tasks_job_id_idx on runsheet_tasks(job_id);

-- Verify:
select column_name, data_type, is_nullable
from information_schema.columns
where table_name = 'runsheet_tasks' and column_name = 'job_id';
