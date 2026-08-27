-- Loaded state for the two packed rows on the driver page.
--
-- Small lamps, cushions and accessories are packed into bags and tubs, so
-- the driver never ticks them individually — they appear as one "N Cushion
-- Bags" row and one "N Accessory Boxes" row. Those rows were tappable but
-- the tick was cosmetic: nothing was stored, it vanished on refresh, and
-- the progress bar ignored it.

alter table jobs add column if not exists cushion_bags_loaded   boolean default false;
alter table jobs add column if not exists accessory_tubs_loaded boolean default false;

-- Verify:
select column_name, data_type from information_schema.columns
where table_name = 'jobs'
  and column_name in ('cushion_bags_loaded','accessory_tubs_loaded');
