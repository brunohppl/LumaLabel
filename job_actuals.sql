-- Actual start and finish per schedule entry, recorded by the crew from the
-- team view. Navigate stamps the start; the Done button stamps the finish.
--
-- This is the training data for everything in the truck & crew planning
-- work: real durations by job type, property size and item count. Until it
-- exists, every plan is built on the flat 60/180-minute guesses.

alter table job_schedule add column if not exists actual_start timestamptz;
alter table job_schedule add column if not exists actual_end   timestamptz;

-- Verify:
select column_name, data_type
from information_schema.columns
where table_name = 'job_schedule'
  and column_name in ('actual_start','actual_end');
