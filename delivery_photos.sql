-- Product photos on delivery lines.
--
-- Programma exports embed one picture per line, anchored to its spreadsheet
-- row. The import now extracts them, stores them, and keeps the URL here.
-- Nothing else about a line changes, and received quantities are untouched.

alter table delivery_lines add column if not exists photo_url text;


-- ── Storage bucket ──────────────────────────────────────────────────
-- Create it if it isn't there, and make it public so the <img> tags can
-- load without a signed URL. The images are product photos from a supplier
-- catalogue, not anything sensitive.
insert into storage.buckets (id, name, public)
values ('delivery-photos', 'delivery-photos', true)
on conflict (id) do update set public = true;


-- ── Policies ────────────────────────────────────────────────────────
-- New buckets have RLS on with no policies, which is what blocked the staff
-- table earlier. These mirror the access the app already has elsewhere.
drop policy if exists "delivery photos readable" on storage.objects;
drop policy if exists "delivery photos writable" on storage.objects;
drop policy if exists "delivery photos updatable" on storage.objects;

create policy "delivery photos readable" on storage.objects
  for select using (bucket_id = 'delivery-photos');

create policy "delivery photos writable" on storage.objects
  for insert with check (bucket_id = 'delivery-photos');

create policy "delivery photos updatable" on storage.objects
  for update using (bucket_id = 'delivery-photos')
  with check (bucket_id = 'delivery-photos');


-- Verify:
select column_name from information_schema.columns
where table_name = 'delivery_lines' and column_name = 'photo_url';

select id, public from storage.buckets where id = 'delivery-photos';
