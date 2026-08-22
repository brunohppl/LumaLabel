-- runsheet_tasks never had a team_id column — it isn't in any migration.
-- The app has been sending it on every save, and the self-healing insert
-- silently dropped it each time, so tasks only ever found their column by
-- matching the vehicle name. Vehicle-less columns (Warehouse) therefore
-- could never show a task: it saved fine and rendered nowhere.

alter table runsheet_tasks add column if not exists team_id  text;
alter table runsheet_tasks add column if not exists kind     text;   -- 'break'
alter table runsheet_tasks add column if not exists category text;   -- transport/styling/warehouse

-- Verify:
select column_name from information_schema.columns
where table_name = 'runsheet_tasks'
  and column_name in ('team_id','kind','category');
