-- Mark a runsheet task as a break rather than work.
--
-- Optional: the app also recognises a break by its title, so it degrades
-- gracefully if this hasn't been run yet. Running it makes the distinction
-- reliable even if someone edits the wording.

alter table runsheet_tasks add column if not exists kind text;

-- Verify:
select column_name, data_type
from information_schema.columns
where table_name = 'runsheet_tasks' and column_name = 'kind';
