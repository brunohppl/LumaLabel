# LUMA Design Co — Warehouse App

Internal tool for LUMA Design Co (Brisbane property staging). Generates printable item labels and packing checklists from a packing-slip PDF, then tracks each job through picking → loading → installation → return via a stylist web view and a mobile-first driver view.

**Live app:** https://lumalabel.onrender.com

---

## Quick orientation

If you're new to this codebase, the mental model is:

1. A stylist uploads a packing-slip PDF on the home page (`/`).
2. The app parses it, generates a **labels PDF** and a **checklist PDF**, and saves a **job** + its **items** to Supabase.
3. The job then moves through a status lifecycle, visible on `/jobs` (stylist view) and `/driver/<job_id>` (driver view).
4. Both views talk to the same Flask backend via a small JSON API; there's no JS framework, build step, or bundler — everything is server-rendered HTML with vanilla JS.

There is **no ORM and no ourselves Postgres connection** — `app.py` talks to Supabase exclusively over its REST API using `urllib`, no SDK. This was a deliberate simplicity choice; see [Architecture notes](#architecture-notes) before changing it.

---

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Flask (Python), `gunicorn` in production | Single `app.py`, no blueprints |
| Hosting | [Render](https://render.com), free tier | Sleeps after inactivity — see [Keeping it awake](#keeping-it-awake) |
| Database | [Supabase](https://supabase.com) (Postgres), free tier | Accessed via REST, not a Postgres driver |
| File storage | Supabase Storage (`item-photos` bucket) | For stylist-taken item photos |
| PDF generation | `reportlab` (labels, checklist), `pdfplumber` (parsing input) | No headless browser involved |
| Frontend | Plain HTML + vanilla JS, no build step | Three templates, no shared JS file (intentional — see below) |

---

## Repo layout

```
app.py                  ← entire backend: parsing, PDF generation, Supabase calls, API routes
templates/
  index.html            ← "/" — label generator (upload PDF, set job owner/date, download PDFs)
  jobs.html             ← "/jobs" — stylist view: job list, picking, notes, photos, delete items
  driver.html           ← "/driver/<job_id>" — mobile driver view: load truck, check off items
requirements.txt
Procfile                ← `web: gunicorn app:app`
supabase_setup.sql      ← run once against a fresh Supabase project
```

There's no `static/` folder — all CSS and JS is inlined in each HTML file. This is intentional: each page is small enough that a shared JS file would add an HTTP request and a sync-drift risk for little benefit. If the app grows substantially, that tradeoff should be revisited.

---

## Local setup

```bash
git clone <repo>
cd <repo>
pip install -r requirements.txt --break-system-packages   # or use a venv
```

Environment variables (set in `.env` locally, or directly in Render's dashboard in production):

| Variable | Required | Purpose |
|---|---|---|
| `SUPABASE_URL` | Yes | Your Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase **anon** key (not service role) |
| `SLACK_WEBHOOK_URL` | No | If set, posts a notification when labels are generated |

> The anon key is also hardcoded as a fallback default in `app.py` (`sb_headers()`) **and** inlined in `jobs.html`'s JS for direct browser→Supabase photo uploads. If you rotate the key, update both places.

Run locally:

```bash
python app.py        # dev server on localhost:5000
```

Or with gunicorn (closer to prod):

```bash
gunicorn app:app
```

---

## Database setup

Run `supabase_setup.sql` once in the Supabase SQL editor. It creates two tables:

### `jobs`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `job_number` | text | e.g. `INV-1269` |
| `job_ref` | text | last 3 digits, e.g. `269` — shown on labels |
| `address` | text | |
| `stage_date` | text | human-readable, e.g. `"12 May 2026"` — **stored as text, not a date column**, see gotcha below |
| `colour` | text | one of the 14 names in `COLOURS` (`app.py`) |
| `status` | text | see [Status lifecycle](#status-lifecycle) |
| `job_owner` | text | stylist-entered name |
| `truck` | text | driver-entered |
| `item_count` | integer | non-extra item count |
| `checked_count` | integer | currently unused by the app logic (kept for future use) |

### `items`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `job_id` | uuid | FK → `jobs.id` |
| `serial` | text | zero-padded, e.g. `"007"` |
| `room` | text | from the packing slip — **deliberately not printed on labels** (see below) |
| `description` | text | |
| `notes` | text | stylist note, shown to driver |
| `is_extra` | boolean | true for blank filler labels / manually added extras |
| `picked` | boolean | stylist has physically picked it |
| `checked` | boolean | driver has loaded it onto the truck |
| `photo_url` | text | public Supabase Storage URL, nullable |

RLS is enabled with permissive `for all using (true)` policies on both tables — there's no per-user auth in this app; anyone with the link can read/write. That's an accepted tradeoff for an internal tool used by a small trusted team. **Do not lift this policy without adding real auth first.**

Supabase Storage also needs a public bucket named `item-photos` (Storage → New bucket → Public). Upload policies:

```sql
create policy "Allow public uploads" on storage.objects for insert with check (bucket_id = 'item-photos');
create policy "Allow public reads"   on storage.objects for select using (bucket_id = 'item-photos');
create policy "Allow public updates" on storage.objects for update using (bucket_id = 'item-photos');
```

---

## Status lifecycle

Jobs move through statuses linearly. The stylist drives the first half, the driver the second half:

```
ready → picking → ready_to_load → loading → loaded → installed → returned
  ↑                                                                  
  └── archived (reachable from any status, restorable)
```

| Status | Set by | UI label | What it means |
|---|---|---|---|
| `ready` | auto, on label generation | "Job Assigned" | Job created, nobody's started |
| `picking` | stylist | "Picking" | Stylist is walking the warehouse |
| `ready_to_load` | stylist | "Ready to Load" | Picking done, waiting on driver |
| `loading` | driver | "Loading" | Driver is loading the truck |
| `loaded` | driver | "Loaded" | Truck loaded, ready to drive |
| `installed` | driver | "Installed" | Install complete on site |
| `returned` | driver | "Returned" | Truck back at warehouse, job fully closed |
| `archived` | stylist (manual) | "Archived" | Hidden from the main view, restorable |

The `/jobs` page groups job tiles **by status** (one section per status, in the order above), and **sorts within each section by installation date ascending** (soonest first). `installed` and `returned` sections always render last regardless of date, by design — they're effectively "done" buckets.

---

## Label generation — the part most likely to need care

`generate_labels()` in `app.py` is the most fiddly function in the codebase because it's positioning text on physical Avery label sheets with zero tolerance for misalignment. Current state:

- **Only one format exists: 18 per page, Avery 62×42-R** (62mm × 42mm, 3 cols × 6 rows, equal 6mm/6.43mm margins-and-gaps). The 9pp (Avery 89×62-R) and 12pp (Avery 80×45-R) code paths that used to live alongside this have been removed entirely — `generate_labels()` only contains 18pp geometry now. The `label_format` parameter is still accepted for backward compatibility but is currently a no-op; if a second format is ever needed again, it'll need to be rebuilt rather than re-enabled.
- **Room name is intentionally not printed on labels.** That space is left blank for the stylist to hand-write — this was a deliberate product decision, not an oversight. Don't "fix" this by re-adding room text without checking with the product owner first.
- Each label shows: colour bar with rotated invoice suffix, date + address on one line at the bottom, item number above that. Font sizes are mostly hardcoded (not auto-fit) after repeated iteration to stop room/date text colliding — see the long comment trail in git history if you need context on why before touching positions again.
- Extras (blank filler labels) are calculated to round the page count up to a full page, then capped/extended to avoid huge waste.

If you need to adjust positions: change one offset at a time and re-render a real job's PDF before touching anything else. Small constant tweaks compound fast on a 42mm-tall label.

---

## Checklist generation

`generate_checklist()` produces the packing checklist PDF. Notable behaviour:

- **Consecutive identical items are grouped into one row** — e.g. six chairs become `6× Dining Chairs` with a serial range `#020–#025`, rather than six separate rows. This was added specifically to reduce page count for warehouse browsing.
- Grouping is naive consecutive-match (same description, adjacent in the sorted list) — it does not re-sort or merge non-adjacent matches. If the packing slip parser ever changes ordering, check this still groups sensibly.
- Job owner appears in the meta block alongside address/date/item count.

---

## Item categorisation (driver view)

`driver.html` groups items by **type**, not room — the rationale is that trucks are loaded with item type in mind (all sofas together, etc.), so drivers navigate the same way. The mapping is a simple keyword-match list (`ITEM_CATEGORIES` in `driver.html`):

```
Sofas, Chairs, Tables, Beds & Mattresses, Storage & Consoles,
Rugs, Lamps & Lighting, Artwork, Soft Furnishings, Outdoor,
Accessories, Extras (+ "Other" catch-all for unmatched descriptions)
```

This list is duplicated nowhere else — it's a single JS array, edit it directly in `driver.html` if new item descriptions start falling into "Other" a lot. There's no admin UI for this; it's a code change.

The **stylist view groups by room** (not type) — this is deliberate, since picking happens room-by-room in the warehouse, while loading happens type-by-type on the truck. Don't unify these without checking that assumption still holds.

---

## Colour assignment

Jobs get a colour from a fixed list of 14 (`COLOURS` in `app.py`), chosen for maximum visual distinctness. Assignment logic (`get_next_colour()`):

- Normally cycles sequentially through the list, persisting the current index to `/tmp/luma_colour_index.txt`.
- **`/tmp` is wiped on every Render restart** (free tier sleeps after inactivity). On restart, instead of defaulting back to colour #0, the app queries Supabase for the most recently created job's colour and picks a random *different* one to start from — this avoids the visually annoying pattern of every cold start landing on the same colour.
- This means colour order isn't strictly guaranteed across a restart boundary, but two colours will also never accidentally repeat back-to-back across a restart.

---

## Photo upload (stylist → driver)

Stylists can attach a photo per item group, visible to the driver as a "View Photo" button.

- Upload happens **directly from the browser to Supabase Storage** (not proxied through Flask) using the anon key inlined in `jobs.html`. This keeps the backend simple but means the anon key is visible in client-side JS — acceptable given the bucket is intentionally public and the app has no auth boundary to begin with.
- Images are compressed client-side (canvas resize to max 800px wide, JPEG quality 0.6) before upload to keep Storage usage small — see `compressImage()` in `jobs.html`.
- The camera icon triggers a hidden `<input type="file" capture="environment">` — there's no custom camera UI, it defers to the OS/browser.

---

## API reference

All routes live in `app.py`. There's no versioning, no auth middleware, no rate limiting.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Health check (pinged externally, see below) |
| GET | `/` | Label generator page |
| GET | `/jobs` | Stylist job board |
| GET | `/driver/<job_id>` | Driver view for one job |
| POST | `/generate` | Body: `{pdfBase64, fileName, installDate?, jobOwner?, labelFormat?}` → binary labels PDF, also saves job to DB |
| POST | `/checklist` | Same body shape → binary checklist PDF (does not save to DB) |
| GET | `/api/jobs` | All jobs, `order=created_at.desc` |
| GET | `/api/jobs/<id>` | `{job, items}` |
| PATCH | `/api/jobs/<id>/status` | Body: `{status, truck?}` |
| POST | `/api/jobs/<id>/items` | Add a manual item: `{serial, room, description, is_extra}` |
| PATCH | `/api/items/<id>/check` | Body: any of `{checked, picked, notes, photo_url}` — partial updates supported |
| DELETE | `/api/items/<id>` | Hard delete |

Two near-identical endpoints (`/generate` and `/checklist`) both parse the uploaded PDF independently rather than sharing a parsed result — this is wasteful but simple, and the parse is fast enough (<1s) that it hasn't mattered in practice.

---

## Keeping it awake

Render's free tier sleeps the instance after ~15 minutes of inactivity, causing a slow cold-start on the next request. [cron-job.org](https://cron-job.org) is configured to hit `/health` every 10 minutes to prevent this. If the app feels slow on first load after a while, check that the cron job is still active — it's an external service, not something configured in this repo.

---

## Architecture notes — things to know before refactoring

- **No shared frontend code.** Status label maps, colour maps, and date-parsing logic are each duplicated across `jobs.html` and `driver.html`. This was a conscious tradeoff against adding a build step for a three-page app. If a fourth page gets added, it's worth reconsidering a shared `static/common.js`.
- **No Supabase SDK.** All DB calls go through hand-rolled `sb_get` / `sb_post` / `sb_patch` / `sb_delete` helpers using `urllib.request` against the REST endpoint. This avoids a dependency but means there's no connection pooling, retries, or typed responses — errors are swallowed silently in several places (e.g. `save_job_to_db`) so a failed save won't crash the request, but also won't surface to the user. If jobs are "going missing," check Render logs first, not just the UI.
- **Stage date is a text field, not a real date column**, formatted like `"12 May 2026"`. All sorting/grouping by date happens client-side in JS by parsing this string back into a `Date` (see `parseStageDate()` in `jobs.html`). This works but is fragile if the date format ever changes upstream in `format_date()` (`app.py`) without updating the parser in lockstep.
- **No automated tests.** Changes are verified by generating a real PDF from a real packing slip and visually checking it. If you add tests, start with `parse_packing_list()` — it's the function most likely to break silently on a new packing-slip layout.

---

## Known rough edges (not bugs, just things worth knowing)

- `checked_count` on `jobs` is written nowhere currently — it exists in the schema but isn't kept in sync. Don't assume it's accurate if you build a feature against it.
- The label generator previously supported 9pp and 12pp formats; both were removed for simplicity (see [Label generation](#label-generation--the-part-most-likely-to-need-care)). If you're hunting for that code in git history, it predates the cleanup that dropped it.
- The role-selection modal (Stylist vs Driver) on `/jobs` re-fetches the job just to populate the modal header, then the chosen view re-fetches it again. Minor redundant network call, not worth fixing unless touching that code anyway.
