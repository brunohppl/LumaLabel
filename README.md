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
| Text segmentation | `wordninja` | Only used to make stylist room-notes readable — see [Room-level notes](#room-level-notes-from-the-packing-slip) |
| Frontend | Plain HTML + vanilla JS, no build step | Four templates, no shared JS file (intentional — see below) |

---

## Repo layout

```
app.py                  ← entire backend: parsing, PDF generation, Supabase calls, API routes
templates/
  index.html            ← "/" — label generator (upload PDF, set job owner/date, download PDFs)
  jobs.html             ← "/jobs" — job list/browse only: search, status grouping, links out to stylist/driver pages
  stylist.html           ← "/stylist/<job_id>" — picking, notes, photos, delete items (split out of jobs.html — see below)
  driver.html           ← "/driver/<job_id>" — mobile driver view: load truck, check off items
requirements.txt
Procfile                ← `web: gunicorn app:app`
supabase_setup.sql      ← run once against a fresh Supabase project
```

There's no `static/` folder — all CSS and JS is inlined in each HTML file. This is intentional: each page is small enough that a shared JS file would add an HTTP request and a sync-drift risk for little benefit. If the app grows substantially, that tradeoff should be revisited.

**`jobs.html` and `stylist.html` used to be one file.** The stylist picking experience originally lived as a slide-in side panel on top of the job list (`/jobs`), opened via a fetch-then-overlay pattern. It was split into its own standalone page, mirroring how `driver.html` already worked, for two reasons: it makes each page easier to size correctly for its actual device (the job list is a desktop/tablet browsing tool; picking happens on a phone in the warehouse), and it lets the two be developed and tested independently without one change risking the other. The "Stylist" button on each job tile now just opens `/stylist/<job_id>` in a new tab — the same pattern the "Driver" button already used for `/driver/<job_id>`. If you're hunting in git history for `closePanel()`, `renderPanelItems()`, or similar — that's where they went.

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

> The anon key is also hardcoded as a fallback default in `app.py` (`sb_headers()`) **and** inlined in `stylist.html`'s JS for direct browser→Supabase photo uploads. If you rotate the key, update both places.

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
| `styling_notes` | text | lead stylist's notes for the whole job, shown at the top of `/stylist/<id>` — distinct from per-room `room_notes` and per-item `notes` below |
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

### `room_notes`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `job_id` | uuid | FK → `jobs.id` |
| `room` | text | matches the `room` value on `items` for the same job |
| `note` | text | parsed from packing-slip bracket text, e.g. `[MOVECHAISEFORSOFATOMEDIA]` → `"Move chaise for sofa to media"` — see [Room-level notes](#room-level-notes-from-the-packing-slip) |

A job can have zero or several rows here — most jobs will have none, since bracket notes are an occasional packing-slip annotation, not something every job has.

RLS is enabled with permissive `for all using (true)` policies on all three tables — there's no per-user auth in this app; anyone with the link can read/write. That's an accepted tradeoff for an internal tool used by a small trusted team. **Do not lift this policy without adding real auth first.**

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
ready → picking → ready_to_load → loading → loaded → installed → archived
```

| Status | Set by | UI label | What it means |
|---|---|---|---|
| `ready` | auto, on label generation | "Job Assigned" | Job created, nobody's started |
| `picking` | stylist | "Picking" | Stylist is walking the warehouse |
| `ready_to_load` | stylist | "Ready to Load" | Picking done, waiting on driver |
| `loading` | driver | "Loading" | Driver is loading the truck |
| `loaded` | driver | "Loaded" | Truck loaded, ready to drive |
| `installed` | driver | "Installed" | Install complete on site |
| `archived` | driver, automatically | "Returned" | Truck back at warehouse, job complete |

**`archived` is the only terminal state, and it's reached automatically — there is no manual archive action anywhere in the UI.** The driver's final button reads "Mark Returned" and sends `status: 'returned'` to the API, but `PATCH /api/jobs/<id>/status` (`app.py`) silently rewrites that to `status: 'archived'` before saving — `'returned'` is never actually persisted to the database, it only exists transiently as what the button click sends. All three frontends display the stored `archived` status as **"Returned"** to the person using them; "archived" is internal naming only, chosen before this auto-completion behaviour existed and kept for backward compatibility with the database column's existing values rather than running a rename migration for a cosmetic difference.

This used to be a two-step, manually-triggered process — an earlier version had a separate `returned` status plus a stylist-facing "Archive" button and a "Restore Job" button to undo it. Both were removed deliberately: archiving a completed job isn't a judgement call that benefits from a manual gate, so making it automatic removed a step without losing anything. If you're looking for that code in git history, it lived in `jobs.html`'s old in-page stylist panel (now `stylist.html`) as `archiveJob()` and a `setStatus('ready')` "Restore" button.

The `/jobs` page groups job tiles **by status** (one section per status, in the order above, `archived` excluded from this grouping entirely), and **sorts within each section by installation date ascending** (soonest first). Archived/returned jobs are hidden from the main view by default — a **"Show Completed Jobs"** toggle reveals them, sorted the same way. There's no way to un-archive a job through the UI; if that's ever needed, it's a direct database edit (`status` back to `'loaded'` or `'installed'`, whichever makes sense for the situation).

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

`driver.html` groups items by **type**, not room — the rationale is that trucks are loaded with item type in mind (all sofas together, etc.), so drivers navigate the same way. The mapping is a simple keyword-match list (`ITEM_CATEGORIES` in `driver.html`), checked **in order** — a description is assigned to the first category whose keyword matches, so order matters whenever a description could plausibly match more than one (e.g. "Bedside Tables" needs `Beds & Mattresses` checked before `Tables`, since both keywords — `bedside` and `table` — appear in the text):

```
Sofas, Chairs, Beds & Mattresses, Tables, Storage & Consoles,
Rugs, Lamps & Lighting, Artwork, Soft Furnishings, Outdoor,
Accessories (+ "Other" catch-all for unmatched descriptions)
```

This list is duplicated nowhere else — it's a single JS array, edit it directly in `driver.html` if new item descriptions start falling into "Other" a lot, or if something lands in the wrong category because of keyword overlap (check match order before just adding a keyword). There's no admin UI for this; it's a code change.

`Extras` is **not** a fixed entry in `ITEM_CATEGORIES` — it only appears as a section if the stylist has manually added extra items to the job (`is_extra: true`). An empty Extras section never renders.

The **stylist view groups by room** (not type) — this is deliberate, since picking happens room-by-room in the warehouse, while loading happens type-by-type on the truck. Don't unify these without checking that assumption still holds.

One grouping subtlety worth knowing: both `stylist.html` and `driver.html` merge **consecutive identical-description items** into a single row (e.g. `6× Dining Chairs #020–#025`) to keep long lists scannable — same idea as the checklist PDF's grouping. `driver.html`'s version additionally splits a merge if the items span more than one **room**, even when the description and serials are otherwise consecutive (confirmed against real data: two adjacent rooms can both have "Accessories (Box)" back-to-back in serial order — without the room-boundary check those would incorrectly merge into one row that hides which room each item is actually in). `stylist.html` doesn't need this check, since it always groups within a single room to begin with.

---

## Room-level notes from the packing slip

Some packing slips include bracket-wrapped instructions for a whole room rather than for a specific item — e.g. `[MOVECHAISEFORSOFATOMEDIA]`. These are parsed out during `parse_packing_list()` (`app.py`) and shown to the stylist next to the relevant room's title on `/stylist/<job_id>`. They are **not** surfaced to the driver at all — there's no code path to this data in `driver.html`, by design, not just by convention (see below).

- **Parsing**: a word containing `[` is treated as the start of a note rather than an item, and everything up to the matching `]` is consumed (handling both a single merged word and the rare case where the PDF inserted spaces inside the brackets) — see `format_room_note()` and the bracket-detection block inside `parse_packing_list()`.
- **Word segmentation**: bracket text is free-form stylist instruction, not a fixed vocabulary like room or item names, so the existing word-splitting helpers (`format_room_name()`, `clean_word()`) don't apply — they rely on a known list of words to break on. Instead, `format_room_note()` uses the `wordninja` package (English word-frequency based segmentation) to turn `MOVECHAISEFORSOFATOMEDIA` into `Move chaise for sofa to media`. This is not perfect on every short or unusual word — short ambiguous substrings can occasionally split oddly (e.g. a word containing "of" might get cut around it) — but it's far more readable than the unspaced alternative, which is the realistic comparison given there's no context to disambiguate with. If this becomes a recurring problem, the fix is a better segmentation approach, not abandoning segmentation entirely.
- **Storage**: notes are saved to their own `room_notes` table (`job_id`, `room`, `note`), not bolted onto `items` or `jobs`. A dedicated table was chosen over reusing the existing per-item `notes` column because a room note doesn't conceptually belong to any one item, and some rooms with a note might have zero items otherwise needing one.
- **Access boundary**: notes are deliberately served from their own endpoint (`GET /api/jobs/<id>/room-notes`), separate from the main `GET /api/jobs/<id>` response that both `stylist.html` and `driver.html` call. This means the driver page has no code path to this data at all, rather than relying on frontend discipline to simply not render a field it could technically access. If you're tempted to fold this into the main job-detail response for convenience, don't — that would silently remove the access boundary.

---

## Colour assignment

Jobs get a colour from a fixed list of 14 (`COLOURS` in `app.py`), chosen for maximum visual distinctness. Assignment logic (`get_next_colour()`):

- Normally cycles sequentially through the list, persisting the current index to `/tmp/luma_colour_index.txt`.
- **`/tmp` is wiped on every Render restart** (free tier sleeps after inactivity). On restart, instead of defaulting back to colour #0, the app queries Supabase for the most recently created job's colour and picks a random *different* one to start from — this avoids the visually annoying pattern of every cold start landing on the same colour.
- This means colour order isn't strictly guaranteed across a restart boundary, but two colours will also never accidentally repeat back-to-back across a restart.

---

## Photo upload (stylist → driver)

Stylists can attach a photo per item group, visible to the driver as a "View Photo" button.

- Upload happens **directly from the browser to Supabase Storage** (not proxied through Flask) using the anon key inlined in `stylist.html`. This keeps the backend simple but means the anon key is visible in client-side JS — acceptable given the bucket is intentionally public and the app has no auth boundary to begin with.
- Images are compressed client-side (canvas resize to max 800px wide, JPEG quality 0.6) before upload to keep Storage usage small — see `compressImage()` in `stylist.html`.
- The camera icon triggers a hidden `<input type="file" capture="environment">` — there's no custom camera UI, it defers to the OS/browser.

---

## API reference

All routes live in `app.py`. There's no versioning, no auth middleware, no rate limiting.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Health check (pinged externally, see below) |
| GET | `/` | Label generator page |
| GET | `/jobs` | Job browse/search list — links out to `/stylist/<id>` and `/driver/<id>`, does no picking/loading itself |
| GET | `/stylist/<job_id>` | Stylist picking view for one job — notes, photos, status, delete items |
| GET | `/driver/<job_id>` | Driver loading view for one job |
| POST | `/generate` | Body: `{pdfBase64, fileName, installDate?, jobOwner?, labelFormat?}` → binary labels PDF, also saves job to DB |
| POST | `/checklist` | Same body shape → binary checklist PDF (does not save to DB) |
| GET | `/api/jobs` | All jobs, `order=created_at.desc` |
| GET | `/api/jobs/<id>` | `{job, items}` |
| GET | `/api/jobs/<id>/room-notes` | Stylist-only — `{room: [note, note, ...]}` parsed from packing-slip bracket text. Not called by `driver.html` at all (see [Room-level notes](#room-level-notes-from-the-packing-slip) below) |
| PATCH | `/api/jobs/<id>/status` | Body: `{status, truck?}`. `status: 'returned'` is silently converted to `'archived'` server-side — see [Status lifecycle](#status-lifecycle) |
| PATCH | `/api/jobs/<id>/notes` | Body: `{styling_notes}` — lead stylist's notes for the job, shown at the top of `/stylist/<id>`, independent of item-level notes |
| POST | `/api/jobs/<id>/items` | Add a manual item: `{serial, room, description, is_extra}` |
| PATCH | `/api/items/<id>/check` | Body: any of `{checked, picked, notes, photo_url}` — partial updates supported |
| DELETE | `/api/items/<id>` | Hard delete |

Two near-identical endpoints (`/generate` and `/checklist`) both parse the uploaded PDF independently rather than sharing a parsed result — this is wasteful but simple, and the parse is fast enough (<1s) that it hasn't mattered in practice.

---

## Keeping it awake

Render's free tier sleeps the instance after ~15 minutes of inactivity, causing a slow cold-start on the next request. [cron-job.org](https://cron-job.org) is configured to hit `/health` every 10 minutes to prevent this. If the app feels slow on first load after a while, check that the cron job is still active — it's an external service, not something configured in this repo.

---

## Architecture notes — things to know before refactoring

- **No shared frontend code.** Status label maps, colour maps, room/item-grouping logic, and progress-ring rendering are each duplicated across `jobs.html`, `stylist.html`, and `driver.html` — three slightly different copies of `STATUS_LABELS`/`STATUS_PILLS` exist because each page only needs the statuses relevant to it (the job list shows all of them; the driver page only cares about its own slice of the lifecycle). `groupItems()` also exists separately in `stylist.html` (groups by room) and `driver.html` (groups by room *and* splits across category boundaries — see the categorisation section above) with genuinely different logic, not just copy-paste drift. This was a conscious tradeoff against adding a build step for a small, slow-changing app. If a fifth page gets added, or if the status/colour maps drift out of sync again (which has happened before — see the "rough edges" section), it's worth reconsidering a shared `static/common.js`.
- **`parseStageDate()` only exists in `jobs.html`.** It's used for sorting/grouping a *list* of jobs by installation date, which only the job-browsing page needs — `stylist.html` and `driver.html` each only ever load one job at a time, so they have no list to sort.
- **No Supabase SDK.** All DB calls go through hand-rolled `sb_get` / `sb_post` / `sb_patch` / `sb_delete` helpers using `urllib.request` against the REST endpoint. This avoids a dependency but means there's no connection pooling, retries, or typed responses — errors are swallowed silently in several places (e.g. `save_job_to_db`) so a failed save won't crash the request, but also won't surface to the user. If jobs are "going missing," check Render logs first, not just the UI.
- **Stage date is a text field, not a real date column**, formatted like `"12 May 2026"`. All sorting/grouping by date happens client-side in JS by parsing this string back into a `Date` (see `parseStageDate()` in `jobs.html`). This works but is fragile if the date format ever changes upstream in `format_date()` (`app.py`) without updating the parser in lockstep.
- **No automated tests.** Changes are verified by generating a real PDF from a real packing slip and visually checking it. If you add tests, start with `parse_packing_list()` — it's the function most likely to break silently on a new packing-slip layout.

---

## Known rough edges (not bugs, just things worth knowing)

- `checked_count` on `jobs` is written nowhere currently — it exists in the schema but isn't kept in sync. Don't assume it's accurate if you build a feature against it.
- The label generator previously supported 9pp and 12pp formats; both were removed for simplicity (see [Label generation](#label-generation--the-part-most-likely-to-need-care)). If you're hunting for that code in git history, it predates the cleanup that dropped it.
- `/jobs` used to open the stylist view as a slide-in panel via a role-selection modal (click a tile → choose Stylist or Driver → panel slides in or new tab opens). That's gone — the tile now has two direct buttons that each open `/stylist/<id>` or `/driver/<id>` in a new tab with no intermediate step. If you're hunting in git history for `openJob()`, `closeRole()`, or a `role-modal` CSS class, that's what they belonged to.
- `wordninja`'s word-frequency segmentation (used for room notes — see [Room-level notes](#room-level-notes-from-the-packing-slip)) occasionally produces an odd split on short ambiguous text (e.g. cutting around "of" mid-word). This is a known limitation of frequency-based segmentation without surrounding context, not a bug to chase — a stylist reading the result understands it despite the artifact, which was the deciding factor over leaving the text unspaced.
