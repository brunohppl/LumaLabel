-- The same tap-to-tap timing, split by install vs pickup.
--
-- nav_events records only "job" or "warehouse" — the kind of work lives
-- on the schedule tile. This joins the two on the job and the day, so an
-- install on Tuesday and that job's pickup three weeks later are counted
-- as the different things they are.
--
-- Same caveats as before: a tap means "leaving", so each gap is the drive
-- PLUS the time on site, and the on-site figure is an estimate. Very
-- short gaps are double-taps, very long ones are missed taps.

with ev as (
  select
    n.id,
    n.vehicle,
    n.created_at,
    n.job_id,
    n.eta_minutes,
    n.destination,
    date(n.created_at at time zone 'Australia/Brisbane') as day,
    lead(n.created_at) over (
      partition by n.vehicle, date(n.created_at at time zone 'Australia/Brisbane')
      order by n.created_at
    ) as next_tap
  from nav_events n
  where n.vehicle is not null
),
typed as (
  select
    ev.*,
    -- What that crew was doing at that job on that day. A job split
    -- across two tiles of the same type still counts once.
    (select js.type
       from job_schedule js
      where js.job_id = ev.job_id
        and js.date   = ev.day
        and js.type in ('install','pickup')
      limit 1) as job_type,
    extract(epoch from (ev.next_tap - ev.created_at)) / 60 as gap_min
  from ev
)
select
  vehicle,
  coalesce(job_type, destination)               as doing,
  count(*)                                      as legs,
  round(avg(gap_min))                           as avg_tap_to_tap,
  round(avg(gap_min - coalesce(eta_minutes,0))) as avg_approx_on_site,
  round(min(gap_min))                           as fastest,
  round(max(gap_min))                           as slowest
from typed
where gap_min is not null
  and gap_min between 5 and 240     -- under 5 is a double tap, over 240 a missed one
group by vehicle, coalesce(job_type, destination)
order by vehicle, doing;


-- The same thing without the vehicle, which is where the numbers will
-- firm up first — there are far more legs per job type than per truck.
with ev as (
  select n.*, date(n.created_at at time zone 'Australia/Brisbane') as day,
         lead(n.created_at) over (
           partition by n.vehicle, date(n.created_at at time zone 'Australia/Brisbane')
           order by n.created_at) as next_tap
  from nav_events n
  where n.vehicle is not null
)
select
  coalesce((select js.type from job_schedule js
             where js.job_id = ev.job_id and js.date = ev.day
               and js.type in ('install','pickup') limit 1),
           ev.destination)                      as doing,
  count(*)                                      as legs,
  round(avg(extract(epoch from (next_tap - created_at))/60))  as avg_tap_to_tap,
  round(avg(extract(epoch from (next_tap - created_at))/60
            - coalesce(eta_minutes,0)))         as avg_approx_on_site
from ev
where next_tap is not null
  and extract(epoch from (next_tap - created_at))/60 between 5 and 240
group by 1
order by legs desc;
