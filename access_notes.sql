-- Access notes for a property (gate codes, lockbox, parking, key location).
--
-- Stored on the JOB, not on the schedule entry: one property has one way in,
-- and a job routinely has several crew tiles across two days. Putting it on
-- the entry would mean typing the gate code once per crew and keeping them
-- in sync by hand.

alter table jobs
  add column if not exists access_notes text;

-- Verify:
select column_name, data_type, is_nullable
from information_schema.columns
where table_name = 'jobs' and column_name = 'access_notes';
