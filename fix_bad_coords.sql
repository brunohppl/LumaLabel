-- Clear coordinates that landed outside Australia.
--
-- The first version of the geocoder passed region=au, which is only a soft
-- bias — an address like "5 Kent Rd" with no suburb could match a US street
-- instead. Those wrong points were cached, so the map kept showing crews on
-- the other side of the world. Clearing them makes the map re-geocode with
-- the country restriction now in place.

-- See what will be cleared first:
select job_ref, address, latitude, longitude from jobs
where latitude is not null
  and (latitude not between -44 and -9 or longitude not between 112 and 154.5);

update jobs
   set latitude = null, longitude = null, geocoded_at = null, geocode_failed = false
 where latitude is not null
   and (latitude not between -44 and -9 or longitude not between 112 and 154.5);
