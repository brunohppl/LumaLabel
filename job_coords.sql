-- Cached geocoding for the map view.
--
-- Jobs store an address but no coordinates: the ETA feature passes address
-- text straight to Google. A map needs points, so each address is geocoded
-- once and the result kept here — the map is then instant and free to open
-- again, and Google is only called for addresses never seen before.
--
-- geocode_failed marks addresses Google could not resolve, so the app stops
-- retrying them on every open (and you can find and fix them).

alter table jobs add column if not exists latitude       double precision;
alter table jobs add column if not exists longitude      double precision;
alter table jobs add column if not exists geocoded_at    timestamptz;
alter table jobs add column if not exists geocode_failed boolean default false;

-- Verify:
select column_name, data_type
from information_schema.columns
where table_name = 'jobs'
  and column_name in ('latitude','longitude','geocoded_at','geocode_failed');
