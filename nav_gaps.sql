-- How long between one tap and the next, per vehicle per day.
--
-- WHAT THIS MEASURES: the gap between setting off for one place and
-- setting off for the next. That span contains the drive AND the time on
-- site, because a tap means "leaving", never "arrived".
--
-- Subtracting the predicted drive (eta_minutes, captured at the moment of
-- the tap) leaves an ESTIMATE of time on site. It is an estimate sitting
-- on an estimate — good for comparing crews and job types, not for
-- billing anyone.
--
-- WHAT BREAKS IT: a missed tap. If a crew forgets to tap at the second
-- job, that job's time is swallowed into the next gap and the figure
-- looks twice as long. The last query flags the long ones so they can be
-- discarded rather than quietly skewing an average.

-- ── 1. Is there enough data to bother? ────────────────────────────────
select count(*)                             as taps,
       count(distinct vehicle)              as vehicles,
       count(distinct date(created_at))     as days,
       min(created_at)::date                as first_day,
       max(created_at)::date                as last_day
from nav_events;


-- ── 2. Every leg, in order, with the gap to the next tap ──────────────
with legs as (
  select
    vehicle,
    date(created_at at time zone 'Australia/Brisbane') as day,
    created_at,
    destination,
    job_id,
    eta_minutes,
    lead(created_at) over (
      partition by vehicle, date(created_at at time zone 'Australia/Brisbane')
      order by created_at
    ) as next_tap
  from nav_events
  where vehicle is not null
)
select
  vehicle,
  day,
  to_char(created_at at time zone 'Australia/Brisbane', 'HH24:MI') as set_off,
  destination,
  eta_minutes                                              as drive_predicted,
  round(extract(epoch from (next_tap - created_at)) / 60)  as until_next_tap,
  round(extract(epoch from (next_tap - created_at)) / 60)
    - coalesce(eta_minutes, 0)                             as approx_on_site
from legs
where next_tap is not null
order by day desc, vehicle, created_at;


-- ── 3. The averages, per vehicle ──────────────────────────────────────
-- Gaps over four hours are left out: that is a missed tap or a lunch
-- break, not a job, and one of them would drag an average badly.
with legs as (
  select
    vehicle,
    destination,
    eta_minutes,
    extract(epoch from (
      lead(created_at) over (
        partition by vehicle, date(created_at at time zone 'Australia/Brisbane')
        order by created_at
      ) - created_at)) / 60 as gap_min
  from nav_events
  where vehicle is not null
)
select
  vehicle,
  destination                                   as went_to,
  count(*)                                      as legs,
  round(avg(gap_min))                           as avg_tap_to_tap,
  round(avg(gap_min - coalesce(eta_minutes,0))) as avg_approx_on_site
from legs
where gap_min is not null
  and gap_min between 5 and 240
group by vehicle, destination
order by vehicle, went_to;


-- ── 4. The ones to distrust ───────────────────────────────────────────
with legs as (
  select vehicle,
         date(created_at at time zone 'Australia/Brisbane') as day,
         created_at,
         extract(epoch from (
           lead(created_at) over (
             partition by vehicle, date(created_at at time zone 'Australia/Brisbane')
             order by created_at) - created_at)) / 60 as gap_min
  from nav_events
  where vehicle is not null
)
select vehicle, day,
       to_char(created_at at time zone 'Australia/Brisbane', 'HH24:MI') as set_off,
       round(gap_min) as gap_min
from legs
where gap_min > 240
order by day desc;
