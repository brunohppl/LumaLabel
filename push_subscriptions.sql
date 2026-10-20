-- One row per device that has agreed to receive notifications.
--
-- A subscription is issued by the browser's push service, not by us, and
-- it identifies a single browser on a single device. The same person on a
-- phone and a laptop is two rows.
--
-- Subscriptions expire or are revoked without warning — a phone reset, a
-- browser cleared, permission withdrawn. The sender removes any that come
-- back rejected, so the table stays honest about who can be reached.

create table if not exists push_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  endpoint    text not null unique,     -- the push service's address for this device
  p256dh      text not null,            -- the device's public key
  auth        text not null,            -- the device's auth secret
  label       text,                     -- optional: whose phone this is
  user_agent  text,
  created_at  timestamptz not null default now(),
  last_sent_at timestamptz
);

create index if not exists push_subs_created_idx on push_subscriptions (created_at desc);

-- New tables have RLS on with no policies, which blocks the app's key.
alter table push_subscriptions enable row level security;
drop policy if exists "push subs select" on push_subscriptions;
drop policy if exists "push subs insert" on push_subscriptions;
drop policy if exists "push subs update" on push_subscriptions;
drop policy if exists "push subs delete" on push_subscriptions;
create policy "push subs select" on push_subscriptions for select using (true);
create policy "push subs insert" on push_subscriptions for insert with check (true);
create policy "push subs update" on push_subscriptions for update using (true) with check (true);
create policy "push subs delete" on push_subscriptions for delete using (true);

-- Verify:
select count(*) from push_subscriptions;
