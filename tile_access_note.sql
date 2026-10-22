-- A per-crew access note on a schedule tile.
--
-- Access notes live on the job and are shared by every crew on it, which
-- is right most of the time. This allows one tile to carry its own — a
-- pickup crew told to use a different entrance, say — without disturbing
-- the shared note the others rely on.
--
-- Empty means "use the job's note", so an ordinary job is unaffected.

alter table job_schedule add column if not exists access_note text;

-- Supabase stops auto-granting Data API access to new tables from 30 Oct
-- 2026. This is a column on an existing table, so its grants are already
-- in place — but new TABLES from here will need them written in.

-- Verify:
select column_name, data_type, is_nullable
from information_schema.columns
where table_name = 'job_schedule' and column_name = 'access_note';
