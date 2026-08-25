-- Clear the geocode_failed flag.
--
-- The first version of the map marked a job as permanently unresolvable on
-- ANY geocoding failure — including a bad key or a network blip, which have
-- nothing to do with the address. Jobs blacklisted that way would never be
-- retried. Run this once after fixing the underlying problem.

update jobs set geocode_failed = false where geocode_failed = true;

-- How many jobs still lack coordinates:
select count(*) as without_coords from jobs
where address is not null and latitude is null;
