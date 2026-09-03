import os
import re
import base64
import tempfile
import json
import urllib.request
import urllib.parse
import urllib.error
from io import BytesIO
from datetime import datetime

import pdfplumber
from flask import Flask, request, jsonify, Response
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

app = Flask(__name__)

# ── Deliveries (separate department, isolated module) ──
# Registered defensively: if deliveries.py has an import error or any other
# problem, the warehouse app keeps running exactly as before — only the
# /deliveries routes go missing. Nothing in this app depends on it.
try:
    from deliveries import deliveries_bp
    app.register_blueprint(deliveries_bp)
except Exception as _deliveries_err:
    print(f'[deliveries] blueprint not loaded: {_deliveries_err}')

# ── Monday.com config ──
MONDAY_TOKEN = os.environ.get('MONDAY_API_TOKEN', 'eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjY5MDQ1MDg0OCwiYWFpIjoxMSwidWlkIjoxMDYyNjU1MjksImlhZCI6IjIwMjYtMDgtMDZUMTE6MjI6MTYuMDAwWiIsInBlciI6Im1lOndyaXRlIiwiYWN0aWQiOjIxMjU3NDI4LCJyZ24iOiJhcHNlMiJ9.mAS-Mwi35B0avY5TcDMwzoXkN5NrSRlXfDaZcx8nOM8')
MONDAY_BOARD_ID = '1853777501'
MONDAY_API_URL  = 'https://api.monday.com/v2'

def monday_query(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    req = urllib.request.Request(
        MONDAY_API_URL,
        data=json.dumps(payload).encode(),
        headers={
            'Content-Type':  'application/json',
            'Authorization': MONDAY_TOKEN,
            'API-Version':   '2024-01',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            try:
                return json.loads(raw)
            except Exception:
                return {'errors': [f'Non-JSON response: {raw[:500].decode("utf-8","replace")}']}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except Exception:
            return {'errors': [f'HTTP {e.code}: {raw[:500].decode("utf-8","replace")}']}
    except Exception as e:
        return {'errors': [str(e)]}


SUPABASE_URL  = os.environ.get('SUPABASE_URL', 'https://aqgxojawmohhogkhcxdb.supabase.co')
SUPABASE_KEY  = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFxZ3hvamF3bW9oaG9na2hjeGRiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzg3NDc5ODYsImV4cCI6MjA5NDMyMzk4Nn0.-2UOdGY52jDEmCmBBtQA2XEy6dVT8ZPA_AIPcM7RFX4')
SUPABASE_REST = SUPABASE_URL + '/rest/v1'

# Supabase write errors used to be swallowed by a bare `except: return None`,
# so a constraint violation and a row that simply didn't match looked identical
# from the browser: "Save failed" with no reason. Every failure now records
# what actually went wrong, and the write endpoints pass it back.
_SB_LAST_ERROR = None


def _sb_note_error(e, table='', payload=None):
    global _SB_LAST_ERROR
    try:
        if isinstance(e, urllib.error.HTTPError):
            body = e.read().decode('utf-8', 'replace')[:400]
            # Translate the two failures that actually reach users into plain
            # English; the raw Postgres text is kept in the server log.
            if 'duplicate key value' in body:
                _SB_LAST_ERROR = ('That name is already used by another column on this day — '
                                  'rename it or edit the existing column instead.')
            elif 'violates foreign key' in body:
                _SB_LAST_ERROR = 'That refers to something that no longer exists — try refreshing the page.'
            else:
                _SB_LAST_ERROR = f'{table}: HTTP {e.code} — {body}'
        else:
            _SB_LAST_ERROR = f'{table}: {type(e).__name__} — {str(e)[:300]}'
    except Exception:
        _SB_LAST_ERROR = f'{table}: {str(e)[:300]}'
    print(f'[supabase] write failed — {_SB_LAST_ERROR} | payload={payload}', flush=True)
    return None


def sb_last_error():
    return _SB_LAST_ERROR


def _sb_clear_error():
    global _SB_LAST_ERROR
    _SB_LAST_ERROR = None


def sb_headers():
    return {
        'apikey':        SUPABASE_KEY,
        'Authorization': 'Bearer ' + SUPABASE_KEY,
        'Content-Type':  'application/json',
        'Prefer':        'return=representation',
    }

def sb_get(table, params=''):
    url = f'{SUPABASE_REST}/{table}?{params}'
    req = urllib.request.Request(url, headers=sb_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return []

MISSING_COL_RE = re.compile(r"Could not find the '([^']+)' column")


def _missing_column(err_text):
    """PostgREST names the offending column when a payload key doesn't exist."""
    if not err_text or 'PGRST204' not in err_text:
        return None
    m = MISSING_COL_RE.search(err_text)
    return m.group(1) if m else None


def sb_post(table, data, _depth=0):
    _sb_clear_error()
    url     = f'{SUPABASE_REST}/{table}'
    payload = json.dumps(data).encode()
    req     = urllib.request.Request(url, data=payload, headers=sb_headers(), method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        _sb_note_error(e, table, data)
        # One unknown key used to sink the whole insert. Drop it and retry, so
        # a single stale field name can't silently break every write. Bounded,
        # and each drop is logged.
        col = _missing_column(_SB_LAST_ERROR)
        if col and _depth < 5 and isinstance(data, dict) and col in data:
            print(f'[supabase] dropping unknown column {table}.{col} and retrying', flush=True)
            return sb_post(table, {k: v for k, v in data.items() if k != col}, _depth + 1)
        return None

def sb_patch(table, params, data, _depth=0):
    _sb_clear_error()
    url     = f'{SUPABASE_REST}/{table}?{params}'
    payload = json.dumps(data).encode()
    hdrs    = {**sb_headers(), 'Prefer': 'return=representation'}
    req     = urllib.request.Request(url, data=payload, headers=hdrs, method='PATCH')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        _sb_note_error(e, table, data)
        col = _missing_column(_SB_LAST_ERROR)
        if col and _depth < 5 and isinstance(data, dict) and col in data:
            print(f'[supabase] dropping unknown column {table}.{col} and retrying', flush=True)
            return sb_patch(table, params, {k: v for k, v in data.items() if k != col}, _depth + 1)
        return None

def sb_delete(table, params):
    _sb_clear_error()
    url = f'{SUPABASE_REST}/{table}?{params}'
    req = urllib.request.Request(url, headers=sb_headers(), method='DELETE')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True
    except Exception as e:
        _sb_note_error(e, table)
        return False

def save_job_to_db(meta, items, colour_name, job_owner='', is_transfer=False,
                   transfer_from_job_id=None, install_date_iso=None):
    """Save job and items to Supabase. Called after label generation.

    install_date_iso: the YYYY-MM-DD date entered by the user on the label
    form. When provided, seeds the standard two-day schedule (load day +
    install day). On re-upload, the old schedule is replaced if a date is
    provided — old entries are wiped in seed_two_day_schedule via sb_delete.
    If no install_date_iso is provided, the schedule is left as-is so
    existing manual schedule adjustments survive a colour/owner re-print."""
    try:
        meta['job_owner'] = job_owner
        job_ref = re.sub(r'\D', '', meta['job_number'])[-3:] if meta['job_number'] else '000'
        # Upsert job record
        job_data = {
            'job_number':  meta['job_number'],
            'job_ref':     job_ref,
            'address':     meta['address'],
            'stage_date':  meta['stage_date'],
            'colour':      colour_name,
            # A job with no install date isn't ready for anything yet — it's
            # waiting for a date. Setting one later moves it back to 'ready'.
            'status':      'ready' if install_date_iso else 'on_hold',
            'job_owner':   meta.get('job_owner', ''),
            'item_count':  len([i for i in items if not i.get('is_extra')]),
            'is_transfer': is_transfer,
            'transfer_from_job_id': transfer_from_job_id if is_transfer else None,
        }
        # Delete existing items if re-generating (keeps the job row itself)
        existing = sb_get('jobs', f'job_number=eq.{meta["job_number"]}')
        if existing:
            job_id = existing[0]['id']
            sb_delete('items',      f'job_id=eq.{job_id}')
            sb_delete('room_notes', f'job_id=eq.{job_id}')
            sb_patch('jobs', f'id=eq.{job_id}', job_data)
        else:
            result = sb_post('jobs', job_data)
            if result:
                job_id = result[0]['id']
            else:
                return

        # Insert items
        items_data = [
            {
                'job_id':           job_id,
                'serial':           item['serial'],
                'room':             item['room'],
                'description':      item.get('description', ''),
                'is_extra':         item.get('is_extra', False),
                'checked':          False,
                'on_truck':         False,
                'photo_url':        None,
                'is_transfer_item': False,
                'not_transferring': False,
            }
            for item in items
        ]
        sb_post('items', items_data)

        # Insert room notes parsed from bracket text, e.g. "[MOVE...]"
        room_notes = meta.get('room_notes', {})
        notes_data = [
            {'job_id': job_id, 'room': room, 'note': note}
            for room, notes in room_notes.items()
            for note in notes
        ]
        if notes_data:
            sb_post('room_notes', notes_data)

        # Seed the two-day schedule if an install date was provided.
        # Pass items so bedroom count can drive smart vehicle assignment.
        # On re-upload with a new date, this replaces any existing schedule.
        # On re-upload without a date, existing schedule is left untouched.
        # Attach any Monday placeholder for this address FIRST, so that the
        # seeding below absorbs it (seeding clears the job's entries before
        # recreating them, carrying the Monday link onto the new tile). Doing
        # this the other way round left the placeholder alongside the seeded
        # tiles — a duplicate for the same job on the same day.
        link_placeholder_schedule_entries(job_id, meta['address'])

        if install_date_iso:
            seed_two_day_schedule(job_id, install_date_iso, 'install', items=items)

    except Exception as e:
        pass  # Never let DB failure break label generation



def street_only(address):
    """Trim a stored address like "7 Forfar Street, Seventeen Mile" down
    to just the number/street part — "7 Forfar Street" — for places like
    the Slack ETA line where the suburb adds length without adding
    anything useful (the job ref already tells you which job it is).
    Addresses are consistently stored as "<number/street>, <suburb>"
    (confirmed across every real packing slip on file), so splitting on
    the first comma is reliable; if there's no comma — the
    "Address not found" fallback, or a manually-typed address without
    one — this just returns the address unchanged rather than mangling
    it or producing an empty string.
    """
    if not address:
        return address
    return address.split(',')[0].strip()


def notify_slack_eta(job, role, eta_text):
    """Post an ETA to Slack — only called when someone explicitly taps
    the address on /driver or /stylist and a location is captured, never
    on a schedule or independently of that tap. Posts as the shared
    "Luma Warehouse" bot identity (same as every other notification this
    app sends) since there's no per-person Slack login here — see the
    longer discussion on real per-person posting requiring a full OAuth
    app and individual authorization, which this deliberately doesn't
    attempt. Set SLACK_WEBHOOK_URL as an environment variable in Render.

    One-liner by request — a previous version used a full Block Kit
    layout (header + a 3-field section + a footer context line), which
    was more than needed for something meant to be glanced at quickly.
    Truck name (e.g. "Nigel") is included for the truck role, since
    that's genuinely useful here — multiple trucks could be out at once,
    and "🚛 ETA" alone wouldn't say which one. There's no equivalent for
    the stylist role since a stylist isn't a named vehicle; the line
    just omits that part rather than printing a misleading placeholder.

    Address is trimmed to just the street (see street_only()) — the
    suburb doesn't add anything useful here and only makes the line
    longer than it needs to be for something meant to be read at a
    glance.
    """
    webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
    if not webhook_url:
        return False

    ref = job.get('job_ref') or job.get('job_number') or ''
    address = street_only(job.get('address', ''))

    if role == 'truck':
        truck_name = job.get('truck') or ''
        who = f'🚛 {truck_name}' if truck_name else '🚛 Truck'
    else:
        who = '🚗 Stylist'

    text = f'{who} — Job {ref} — {address} — ETA {eta_text}'

    message = {
        'username': 'Luma Warehouse',
        'icon_emoji': ':truck:' if role == 'truck' else ':car:',
        'text': text,
    }

    try:
        data = json.dumps(message).encode('utf-8')
        req  = urllib.request.Request(
            webhook_url,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        print(f'[ETA Slack] Failed to post: {type(e).__name__}: {e}')
        return False


def get_truck_eta(lat, lng, destination_address):
    """Look up driving time from (lat, lng) to destination_address using
    Google's Distance Matrix API, and return the estimated *arrival
    clock time* (e.g. "9:15am") rather than a duration like "14 mins" —
    a clock time is what actually shows on the job tile. Set
    GOOGLE_MAPS_API_KEY as an environment variable in Render. The
    destination is passed as plain text — Google geocodes it
    server-side, so no separate geocoding step is needed here.

    Uses duration.value (seconds, an int) rather than parsing
    duration.text ("1 hour 5 mins") back into minutes — far less fragile
    than string-parsing Google's human-readable text.

    Time zone: the arrival time is computed in UTC then explicitly
    converted to Australia/Brisbane before formatting, rather than
    trusting the server's local clock — Render's container could be
    running in any timezone, and silently using server local time here
    would show a clock time that's wrong by however many hours the
    server's timezone differs from the warehouse's. Brisbane doesn't
    observe daylight saving, so a fixed zone name is correct year-round
    with no DST edge cases.

    Returns the formatted arrival time string on success, or None on any
    failure (missing key, network error, address not found, etc.) —
    callers should treat None as "couldn't calculate an ETA right now"
    and fail quietly toward the driver, the same way notify_slack_eta() does
    when its webhook isn't configured. Every failure path is printed to
    stdout (visible in Render's logs) since this silently returning None
    gave no way to diagnose a misconfigured key, disabled API, or
    billing issue from outside the server.
    """
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        print('[ETA] GOOGLE_MAPS_API_KEY is not set')
        return None
    try:
        import time as _time
        params = urllib.parse.urlencode({
            'origins':          f'{lat},{lng}',
            'destinations':     destination_address,
            'units':            'metric',
            'departure_time':   'now',          # enables traffic-aware duration
            'traffic_model':    'best_guess',   # realistic estimate vs optimistic/pessimistic
            'key':              api_key,
        })
        url = f'https://maps.googleapis.com/maps/api/distancematrix/json?{params}'
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as r:
            result = json.loads(r.read())
        print(f'[ETA] Distance Matrix response: {result}')
        if result.get('status') != 'OK':
            print(f'[ETA] Top-level status not OK: {result.get("status")} — {result.get("error_message", "")}')
            return None
        element = result['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            print(f'[ETA] Element status not OK: {element.get("status")}')
            return None

        # Prefer duration_in_traffic (live traffic) — falls back to duration
        # if traffic data isn't available for this route
        dur = element.get('duration_in_traffic') or element.get('duration')
        if not dur:
            print('[ETA] No duration in response')
            return None
        duration_seconds = dur['value']
        from zoneinfo import ZoneInfo
        from datetime import timedelta, timezone as _tz
        now_utc      = datetime.now(_tz.utc)
        arrival_utc  = now_utc + timedelta(seconds=duration_seconds)
        arrival_local = arrival_utc.astimezone(ZoneInfo('Australia/Brisbane'))
        # e.g. "9:15am" — lowercase am/pm, no leading zero on the hour
        return arrival_local.strftime('%-I:%M%p').lower()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        print(f'[ETA] HTTPError {e.code}: {body}')
        return None
    except Exception as e:
        print(f'[ETA] Unexpected error: {type(e).__name__}: {e}')
        return None


# ── Runsheet schedule constants ──
# Vehicles: van (Marlin, used by stylists) + three trucks.
# Workers split by role — both lists are combined for the full
# worker dropdown; keeping them separate lets the UI group them.
TRANSPORT_VEHICLES = ['Bruce', 'Nigel', 'Nemo']
STYLING_VEHICLES   = ['Marlin', 'VUG', 'Deb', 'Flo']
OTHER_VEHICLES     = ['Own Car']  # team editor also offers this — must validate cleanly
RUNSHEET_VEHICLES  = TRANSPORT_VEHICLES + STYLING_VEHICLES + OTHER_VEHICLES

RUNSHEET_STYLISTS = ['Addy', 'Montie', 'Delphine', 'India', 'Hayley', 'Lyndall',
                     'Gill', 'Carolina']
RUNSHEET_DRIVERS  = ['Jo', 'Savio', 'Nick', 'Ayoub', 'Bruno', 'Phil', 'Thiago', 'Max']
RUNSHEET_WORKERS  = RUNSHEET_STYLISTS + RUNSHEET_DRIVERS

# Time slots: 07:30 to 15:30 in 30-minute increments — matches the
# actual transport day. Generated rather than hand-typed to avoid gaps.
def _build_time_slots():
    slots = []
    h, m = 7, 30
    while (h, m) <= (15, 30):
        slots.append(f'{h:02d}:{m:02d}')
        m += 30
        if m == 60:
            m, h = 0, h + 1
    return slots

RUNSHEET_TIME_SLOTS = _build_time_slots()

# Duration options in minutes — 30-min steps from 30 min to 4 hrs.
RUNSHEET_DURATIONS = list(range(30, 570, 30))  # [30, 60, ..., 540] — a full
# working day. The old 240 cap silently rejected any tile stretched past
# four hours, which looked like the resize simply not saving.


# ── Colour cycle ────────────────────────────────────────────────
# Chosen by maximising the smallest perceptual (CIE Lab) distance between any
# two, so no two look alike on a printed label. The smallest gap here is
# ΔE 36.7; the previous palette had pairs as close as ΔE 12 (Green vs Olive,
# Teal vs Cyan) which were effectively the same colour on paper.
#
# 'text' is the colour to print ON the bar — pale colours need dark text.
COLOURS = [
    {'hex': '#C62828', 'name': 'Red', 'text': 'white'},
    {'hex': '#D97700', 'name': 'Orange', 'text': 'black'},
    {'hex': '#D9CE00', 'name': 'Yellow', 'text': 'black'},
    {'hex': '#AABF69', 'name': 'Sage', 'text': 'black'},
    {'hex': '#00D900', 'name': 'Bright Green', 'text': 'black'},
    {'hex': '#008C0E', 'name': 'Green', 'text': 'black'},
    {'hex': '#475900', 'name': 'Olive', 'text': 'white'},
    {'hex': '#36D998', 'name': 'Mint', 'text': 'black'},
    {'hex': '#0B7359', 'name': 'Teal', 'text': 'white'},
    {'hex': '#00B8D9', 'name': 'Cyan', 'text': 'black'},
    {'hex': '#0077D9', 'name': 'Sky', 'text': 'black'},
    {'hex': '#0029A6', 'name': 'Indigo', 'text': 'white'},
    {'hex': '#0000D9', 'name': 'Blue', 'text': 'white'},
    {'hex': '#004359', 'name': 'Navy', 'text': 'white'},
    {'hex': '#4F1659', 'name': 'Purple', 'text': 'white'},
    {'hex': '#D900C3', 'name': 'Magenta', 'text': 'black'},
    {'hex': '#D9006C', 'name': 'Cerise', 'text': 'white'},
    {'hex': '#D977A8', 'name': 'Pink', 'text': 'black'},
    {'hex': '#D99E77', 'name': 'Tan', 'text': 'black'},
    {'hex': '#592416', 'name': 'Brown', 'text': 'white'},
    {'hex': '#757575', 'name': 'Grey', 'text': 'white'},
]

# Colours retired from the picker. Kept only so a job labelled before the
# palette changed still prints in the colour it was actually given — the crew
# is holding that physical label. Never offered for a new job.
LEGACY_COLOURS = [
    # Older shades of names that are still in the picker — an existing job
    # keeps the exact colour it was printed in.
    {'hex': '#6A0DAD', 'name': 'Purple (old)', 'text': 'white'},
    {'hex': '#F9A825', 'name': 'Yellow (old)',  'text': 'black'},
    {'hex': '#00838F', 'name': 'Teal (old)',    'text': 'white'},
    {'hex': '#AD1457', 'name': 'Magenta (old)', 'text': 'white'},
    {'hex': '#558B2F', 'name': 'Olive (old)',   'text': 'white'},
    {'hex': '#00ACC1', 'name': 'Cyan (old)',    'text': 'black'},
    {'hex': '#F06292', 'name': 'Pink (old)',    'text': 'black'},
    {'hex': '#E65100', 'name': 'Orange (old)',  'text': 'white'},
    {'hex': '#1565C0', 'name': 'Blue (old)',    'text': 'white'},
    {'hex': '#2E7D32', 'name': 'Green (old)',   'text': 'white'},
    {'hex': '#283593', 'name': 'Indigo (old)',  'text': 'white'},
    {'hex': '#D62828', 'name': 'Red (old)',     'text': 'white'},
    {'hex': '#4E342E', 'name': 'Brown (old)',   'text': 'white'},
]


def find_colour(name):
    """Resolve a stored colour name, including ones no longer offered."""
    if not name:
        return None
    for c in COLOURS:
        if c['name'] == name:
            return c
    for c in LEGACY_COLOURS:
        if c['name'] == name or c['name'].replace(' (old)', '') == name:
            return c
    return None


# Persistent colour index stored in a simple file
COLOUR_INDEX_FILE = '/tmp/luma_colour_index.txt'

def get_colours_out_at_warehouse():
    """Colours currently in use by jobs that are assigned but not yet picked up (status='ready')."""
    try:
        jobs = sb_get('jobs', "status=eq.ready&select=colour")
        return [j['colour'] for j in jobs if j.get('colour')]
    except Exception:
        return []

def get_next_colour(manual_name=None):
    import random as _random

    # ── Manual selection takes priority ──
    if manual_name:
        match = find_colour(manual_name)
        if match:
            return match
        # Unknown name — fall through to auto logic below

    # ── Auto selection: avoid any colour currently out at the warehouse ──
    taken = set(get_colours_out_at_warehouse())
    available = [c for c in COLOURS if c['name'] not in taken]
    pool = available if available else COLOURS  # if every colour is taken, allow reuse

    try:
        with open(COLOUR_INDEX_FILE, 'r') as f:
            idx = int(f.read().strip())
        next_idx = (idx + 1) % len(COLOURS)
    except:
        # File missing = restart — look up last used colour from Supabase
        # and pick a random different one
        last_colour = None
        try:
            jobs = sb_get('jobs', 'order=created_at.desc&limit=1')
            if jobs:
                last_colour = jobs[0].get('colour')
        except:
            pass
        if last_colour:
            used_idx  = next((i for i, c in enumerate(COLOURS) if c['name'] == last_colour), None)
            available_idx = [i for i in range(len(COLOURS)) if i != used_idx]
            next_idx  = _random.choice(available_idx)
        else:
            next_idx = _random.randint(0, len(COLOURS) - 1)
        idx = next_idx
        next_idx = (idx + 1) % len(COLOURS)

    with open(COLOUR_INDEX_FILE, 'w') as f:
        f.write(str(next_idx))

    chosen = COLOURS[idx]
    # If the sequential pick is taken and alternatives exist, swap to one that's free
    if chosen['name'] in taken and available:
        chosen = _random.choice(available)
    return chosen

# ── Room headers detected dynamically — no hardcoded list needed ──
SKIP_WORDS = [
    'Description','Quantity','EXTENSIONRATE','LUMADesignCoPtyLtd',
    'Unit223PerivaleSt','DARRAQLD4076','AUSTRALIA','ABN','Reference',
    'InvoiceDate','InvoiceNumber','PACKINGSLIP','96675056201',
    'EXTENSIONRATE','PACKING','SLIP',
]
SKIP_PATTERNS_WORDS = [
    r'^QU-',r'^\d+\.\d{2}$',r'^96\d+',r'p/week',r'weekhire',
    r'Unconditional',r'priortoend',r'collectionwill',r'notextending',
    r'extensionrate',r'Paymentof',r'Ifnotextending',r'Extensionrate',
]

def is_room_header(word, next_word=None):
    """Detect room headers dynamically.
    A word is a room header if its significant letters are all uppercase
    (ignoring ordinals like 2nd, 3rd).

    Previously this also required the next word to be a quantity like
    "1.00", on the theory that a genuine item description would never be
    fully uppercase. Real packing slips confirmed that assumption holds —
    but the quantity-adjacency check itself was unreliable: pdfplumber's
    word extraction interleaves the right-hand quantity column into the
    word stream based on visual position, and if a room's quantity value
    is missing from the source PDF for any reason (seen in practice — see
    "7 Forfar St" job, where LIVING ROOM had no "1.00" on its row at all),
    the room header was silently swallowed as an item under the previous
    room instead of starting a new group. The `next_word` parameter is
    kept for backward compatibility with any external callers but is no
    longer used.
    """
    import re as _re
    # Strip leading ordinal prefix (2nd, 3rd etc.) before checking case
    stripped = _re.sub(r'^\d+(st|nd|rd|th)', '', word, flags=_re.I)
    letters_only = _re.sub(r'[^a-zA-Z]', '', stripped)
    if not letters_only:
        return False
    # Must be all uppercase, and at least 2 letters (avoid single stray
    # capital letters or initials being mistaken for a room)
    return letters_only == letters_only.upper() and len(letters_only) >= 2

def format_room_name(raw):
    """Convert merged all-caps room names to readable format.
    e.g. FRONTDECK -> Front Deck, MASTERBEDROOM -> Master Bedroom
    """
    import re as _re

    # Handle leading digit prefix like '2nd'
    prefix = ''
    m = _re.match(r'^(\d+\w{0,2})(.*)', raw)
    if m and _re.match(r'^\d', m.group(1)):
        prefix = m.group(1) + ' '
        raw = m.group(2)

    # Insert spaces before known room word boundaries
    BREAKS = ['OUTDOOR','LIVING','DINING','SITTING','MASTER','FRONT','BACK',
              'LAUNDRY','KITCHEN','HALLWAY','HALLW','STUDY','ENTRY','GARAGE',
              'PATIO','GARDEN','MEDIA','OFFICE','BEDROOM','BATHROOM','BATH',
              'DECK','ROOM']
    result = raw
    for word in sorted(BREAKS, key=len, reverse=True):
        result = _re.sub(f'({word})', r' \1', result, flags=_re.I)
    result = result.strip()

    # Title case
    parts = (prefix + result).split()
    titled = ' '.join(w.capitalize() for w in parts)

    # Fix common joins
    fixes = [
        ('Bed Room', 'Bedroom'),
        ('Bath Room', 'Bathroom'),
        ('Hall Way', 'Hallway'),
    ]
    for wrong, right in fixes:
        titled = titled.replace(wrong, right)

    # Handle standalone room words that imply a full name
    STANDALONE_MAP = {
        'Master': 'Master Bedroom',
        'Outdoor': 'Outdoor Area',
        'Hallway': 'Hallway',
        'Laundry': 'Laundry',
        'Study': 'Study',
        'Kitchen': 'Kitchen',
        'Bathroom': 'Bathroom',
    }
    if titled in STANDALONE_MAP:
        titled = STANDALONE_MAP[titled]

    # Separate trailing digits: Bedroom2 -> Bedroom 2
    titled = _re.sub(r'([A-Za-z])([0-9])', lambda m: m.group(1) + ' ' + m.group(2), titled)

    return titled

# ════════════════════════════════════════════════
# ════════════════════════════════════════════════
# PARSE PACKING LIST — LUMA format (word-based)
# ════════════════════════════════════════════════
def clean_word(w):
    s = w
    s = re.sub(r'''''(\d+x)([A-Za-z])''''', lambda m: m.group(1)+' '+m.group(2), s)
    s = re.sub(r'''''(\d+)([A-Za-z])''''', lambda m: m.group(1)+' '+m.group(2), s)
    s = re.sub(r'([a-z])([A-Z][a-z])', r'\1 \2', s)
    for pat, repl in [
        (r'(?i)kitchenaccessories','Kitchen Accessories'),
        (r'(?i)doubleensemble','Double Ensemble'),
        (r'(?i)queenensemble','Queen Ensemble'),
        (r'(?i)occasionalchair','Occasional Chair'),
        (r'(?i)coffeetable','Coffee Table'),
        (r'(?i)bedsidetables?','Bedside Tables'),
        (r'(?i)floorlamp','Floor Lamp'),
        (r'(?i)floorrug','Floor Rug'),
        (r'(?i)entertainmentunit','Entertainment Unit'),
        (r'(?i)diningtable','Dining Table'),
        (r'(?i)diningchairs?','Dining Chairs'),
        (r'(?i)outdoortable','Outdoor Table'),
        (r'(?i)outdoorchairs?','Outdoor Chairs'),
        (r'(?i)tablecentrepiece','Table Centrepiece'),
        (r'(?i)towelset','Towel Set'),
        (r'(?i)seatersofa','Seater Sofa'),
    ]:
        s = re.sub(pat, repl, s)
    return s.strip()

def format_room_note(raw):
    """Convert a merged all-caps bracket note into readable text.
    e.g. MOVECHAISEFORSOFATOMEDIA -> Move chaise for sofa to media

    Note text is free-form stylist instructions, not a fixed vocabulary
    like room/item names, so we can't rely on a known word list the way
    format_room_name() and clean_word() do. wordninja segments merged
    text using English word-frequency statistics — not perfect on every
    short or unusual word, but far more readable than leaving it unspaced.
    """
    text = raw.strip('[]').strip()
    if not text:
        return ''
    try:
        import wordninja
        words = wordninja.split(text)
    except Exception:
        words = [text]  # fall back to the raw merged text if segmentation fails
    if not words:
        return ''
    sentence = ' '.join(words)
    return sentence[0].upper() + sentence[1:].lower()

def parse_packing_list(pdf_bytes):
    meta = {'pl_number': '', 'job_number': '', 'address': '', 'stage_date': ''}
    items = []
    room_notes = {}  # room name -> list of note strings, e.g. "[MOVECHAISETOMEDIA]"

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        all_words = []
        for page in pdf.pages:
            all_words.extend([w['text'] for w in page.extract_words()])

    for w in all_words:
        if re.match(r'^INV-\d+$', w) and not meta['job_number']:
            meta['job_number'] = w
        if re.match(r'^STG-\d+', w) and not meta['job_number']:
            meta['job_number'] = w
        m = re.search(r'(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})', w, re.I)
        if m and not meta['stage_date']:
            meta['stage_date'] = m.group(1)+' '+m.group(2).capitalize()+' '+m.group(3)

    # Extract address positionally — grab top-left area of page 1
    # This handles all address formats: "1504/66 Hope St" and "47 Riverview Terrace"
    if not meta['address']:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            page  = pdf.pages[0]
            # Left 42% of page, below header (y>100), top third
            box   = page.within_bbox((0, 100, page.width * 0.42, page.height * 0.32))
            words = box.extract_words()

            # Find the first word that looks like a street address:
            # - starts with a number (47, 1504/66, Unit 2)
            # - or is on a line that contains a number followed by street-like words
            addr_lines = {}
            for w in words:
                y_bucket = round(w['top'] / 8) * 8  # group words on same line
                if y_bucket not in addr_lines:
                    addr_lines[y_bucket] = []
                addr_lines[y_bucket].append(w['text'])

            # Find first line starting with a number or apartment pattern
            for y_pos in sorted(addr_lines.keys()):
                line_text = ' '.join(addr_lines[y_pos])
                # Skip header lines like "PACKING SLIP", "FROM", company names
                if re.match(r'^(PACKING|FROM|SHIP|Luma|Interior|LUMA)', line_text, re.I):
                    continue
                # Match: starts with digits, optional slash, digits
                if re.match(r'^\d', line_text):
                    # Fix merged words
                    addr = re.sub(r'([a-z])([A-Z])', r'\1 \2', line_text)
                    addr = re.sub(r'(\d)([A-Z])', r'\1 \2', addr)
                    addr = re.sub(r',([A-Z])', r', \1', addr)
                    # Strip anything after "Invoic" or similar boilerplate
                    addr = re.sub(r'\s*(Invoic|Invoice|INV-|QU-).*$', '', addr, flags=re.I).strip()

                    # Check if next line has suburb/state info
                    next_lines = [addr_lines[y] for y in sorted(addr_lines.keys()) if y > y_pos]
                    if next_lines:
                        next_text = ' '.join(next_lines[0])
                        # Skip if next line is LUMA's own office address
                        if re.search(r'Darra|Perivale|Indooroopilly', next_text, re.I):
                            next_lines = next_lines[1:] if len(next_lines) > 1 else []
                            if next_lines:
                                next_text = ' '.join(next_lines[0])
                        if re.search(r'QLD|NSW|VIC|WA|SA|TAS|ACT|NT|Brisbane|Sydney|Melbourne', next_text, re.I):
                            # Only take suburb/postcode part — up to 4-digit postcode
                            suburb_m = re.search(r'([\w\s]+(?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT)[\s\d]+)', next_text, re.I)
                            if suburb_m:
                                addr = addr + ', ' + suburb_m.group(1).strip()
                    meta['address'] = addr.strip()
                    break

    if not meta['address']: meta['address'] = 'Address not found'
    # A slip without a date used to be given a RANDOM one 3-14 days out,
    # which then printed on the labels as if it were real. A job without a
    # date now stays dateless and goes on hold instead.

    current_room = None
    serial = 1
    skip_until = -1  # index up to which words have already been consumed by a multi-word bracket note
    for idx, w in enumerate(all_words):
        if idx <= skip_until: continue
        if w in SKIP_WORDS: continue
        if any(re.search(p, w, re.I) for p in SKIP_PATTERNS_WORDS): continue
        # Bracket notes — e.g. "[MOVECHAISEFORSOFATOMEDIA]" — are stylist
        # instructions for the room, not pickable items, and must NEVER be
        # treated as a room header even though they're often written in
        # all-caps just like real room names (confirmed on a real job —
        # "16 Hillview St" — where every bracket note in the document was
        # misidentified as its own room because is_room_header() only
        # checks letter casing). This check runs BEFORE the room-header
        # check below for exactly that reason: a bracket note's casing is
        # irrelevant to whether it's a room, so it must be ruled out first
        # rather than racing against the uppercase check on equal footing.
        # They may come through as a single merged word or split across
        # multiple words if the PDF inserted spaces inside the brackets;
        # either way, consume everything from '[' to the matching ']'.
        if '[' in w:
            bracket_parts = [w]
            j = idx
            if ']' not in w:
                j += 1
                while j < len(all_words) and ']' not in all_words[j]:
                    bracket_parts.append(all_words[j])
                    j += 1
                if j < len(all_words):
                    bracket_parts.append(all_words[j])
            skip_until = j  # don't reprocess the consumed words as items
            note_text = format_room_note(' '.join(bracket_parts))
            if note_text and current_room:
                room_notes.setdefault(current_room, []).append(note_text)
            continue
        # Standalone all-caps parenthetical annotations — e.g. "(NO STYLING)"
        # — are the same class of problem as bracket notes (uppercase text
        # that isn't a room) but carry no useful instruction, so they're
        # discarded entirely rather than kept as a room note or turned into
        # a placeholder item. This check must run before the room-header
        # check below for the same reason as the bracket check above: a
        # room with "(NO STYLING)" right after it (e.g. KITCHEN, BATHROOM)
        # was being misread as a brand new room, silently reassigning
        # current_room for anything that followed. Genuine item
        # descriptions that happen to contain parentheses (e.g.
        # "FloorRug(std)", "DiningTable(2.2max)") always have lowercase
        # letters inside, so checking that the parenthetical content is
        # fully uppercase safely distinguishes an annotation from a real
        # item. A room whose only content is "(NO STYLING)" intentionally
        # ends up with zero items and doesn't appear anywhere downstream —
        # that's the desired behaviour, not a bug to fix with a
        # placeholder.
        if re.match(r'^\(.*\)$', w):
            inner_letters = re.sub(r'[^a-zA-Z]', '', w)
            if inner_letters and inner_letters == inner_letters.upper():
                continue
        # Dynamic room header detection: all-caps word
        next_w = all_words[idx + 1] if idx + 1 < len(all_words) else ''
        if is_room_header(w, next_w):
            current_room = format_room_name(w)
            continue
        if re.match(r'^\d+\.\d{2}$', w): continue
        if not current_room: continue
        name = clean_word(w)
        if not name or len(name) <= 1: continue

        # Detect quantity prefix e.g. "2x Barstools", "4-6x Chairs", "2xBedside"
        qty = 1
        qty_match = re.match(r'^(\d+)(?:\s*[-–]\s*(\d+))?\s*[xX]\s*', name)
        if qty_match:
            # Use highest number in range e.g. "4-6" -> 6
            qty = int(qty_match.group(2)) if qty_match.group(2) else int(qty_match.group(1))
            qty = min(qty, 12)

        # Strip quantity prefix for clean description e.g. "2x Barstools", "2xBedside" -> clean name
        _desc_raw = re.sub(r'^\d+(?:\s*[-–]\s*\d+)?\s*[xX]\s*', '', name).strip()
        # Capitalise first letter only (preserve rest of casing)
        description = _desc_raw[0].upper() + _desc_raw[1:] if _desc_raw else _desc_raw

        # Accessories always get 2 labels — one per box
        if re.search(r'\baccessories\b', description, re.I):
            for _ in range(2):
                items.append({'serial': f'{serial:03d}', 'room': current_room,
                              'description': description})
                serial += 1
            continue

        # Artwork gets 1 label
        if re.search(r'\bartwork\b', description, re.I):
            qty = 1

        # Linen and cushion items get (Bag) suffix
        if re.search(r'\blinen\b|cushion', description, re.I):
            description = description + ' (Bag)'

        # Ensemble label counts: Single → 1 mattress + 1 bed frame
        #                        Double/Queen/King → 1 mattress + 2 bed frames
        if re.search(r'\bensemble\b', description, re.I):
            is_single = re.search(r'\bsingle\b', description, re.I)
            suffixes = ['(Mattress)', '(Bed Frame)'] if is_single else ['(Mattress)', '(Bed Frame)', '(Bed Frame)']
            for suffix in suffixes:
                items.append({'serial': f'{serial:03d}', 'room': current_room, 'description': f'{description} {suffix}'})
                serial += 1
            continue

        for _ in range(qty):
            items.append({'serial': f'{serial:03d}', 'room': current_room, 'description': description})
            serial += 1
            # Dining tables always ship with separate legs — add a Table Legs
            # item in the same room so it appears in the same driver group
            if re.search(r'\bdining table\b', description, re.I):
                items.append({'serial': f'{serial:03d}', 'room': current_room, 'description': 'Table Legs'})
                serial += 1

    meta['room_notes'] = room_notes
    return meta, items

def format_date(raw):
    """Parse a date string and return a compact label like '7 JUL'.
    Used for filenames and PDF checklist headers."""
    if not raw: return '—'
    for fmt in ('%d %B %Y', '%d %b %Y', '%B %d, %Y'):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt.strftime('%-d %b').upper()
        except:
            pass
    m = re.search(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', raw, re.I)
    if m: return m.group(1) + ' ' + m.group(2).upper()
    return raw[:6].upper()


def format_date_label(raw):
    if not raw or not str(raw).strip():
        return 'DATE TBC'
    """Parse a date string and return a prominent label-friendly format
    like 'WED 7th - JUL' for printing on the physical label itself.
    Ordinal suffix (ST/ND/RD/TH) makes the day number unambiguous at a glance."""
    if not raw: return '—'
    dt = None
    for fmt in ('%d %B %Y', '%d %b %Y', '%B %d, %Y', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            break
        except:
            pass
    if not dt:
        # Fall back to the compact version if we can't parse it
        return format_date(raw)
    day = dt.day
    suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    # Short form — 'WED 14th - SEP'. The abbreviated month keeps the line short
    # so it can be set much larger on the label.
    return f"{dt.strftime('%a').upper()} {day}{suffix} - {dt.strftime('%b').upper()}"


# ════════════════════════════════════════════════
# GENERATE LABELS PDF
# ════════════════════════════════════════════════
# Label sheet geometry, keyed by labels per page. Kept as data so a new paper
# stock is a table entry rather than edits scattered through the drawing code.
#   size    — label width x height in mm
#   grid    — columns x rows
#   margin  — distance from the page edge to the first label (x, y) in mm
#   gap     — space between labels (x, y) in mm
LABEL_FORMATS = {
    # 105 x 37 mm, 2 across x 8 down. Two columns of 105mm span the full 210mm
    # width, so there is no side margin and no gap; 8 x 37 = 296mm leaves
    # 0.5mm top and bottom.
    16: {'size': (105.0, 37.0), 'grid': (2, 8), 'margin': (0.0, 0.5), 'gap': (0.0, 0.0)},

    # Avery 62 x 42-R, 3 across x 6 down — the previous stock.
    18: {'size': (62.0, 42.0),  'grid': (3, 6), 'margin': (6.0, 6.43), 'gap': (6.0, 6.43)},
}
DEFAULT_LABEL_FORMAT = 16


AU_STATE_RE = re.compile(
    r'[\s,]+(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\b(?:[\s,]+\d{4})?[\s,]*$', re.I)
AU_POSTCODE_RE = re.compile(r'[\s,]+\d{4}[\s,]*$')


def label_address(addr):
    """Street only — no suburb, state or postcode. The crew already knows the
    job; what they need off the label is the street."""
    if not addr:
        return ''
    out = AU_STATE_RE.sub('', addr.strip())
    out = AU_POSTCODE_RE.sub('', out)      # a bare postcode with no state
    out = out.split(',')[0]                # drop the suburb
    return out.strip().rstrip(',').strip()


def generate_labels(meta, items, colour, label_format=DEFAULT_LABEL_FORMAT):
    colour_hex  = colour['hex']

    # Pale colours need dark text on the bar. The palette says which to use;
    # fall back to measuring the colour so a hand-set hex still works.
    def _readable_on(hex_str):
        try:
            r, g, b = (int(hex_str[i:i+2], 16) / 255 for i in (1, 3, 5))
        except (ValueError, IndexError):
            return '#FFFFFF'
        def lin(v):
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
        return '#FFFFFF' if lum < 0.36 else '#1A1714'

    bar_text = colour.get('text')
    bar_text_hex = ('#FFFFFF' if bar_text == 'white'
                    else '#1A1714' if bar_text == 'black'
                    else _readable_on(colour_hex))
    date_txt    = format_date_label(meta['stage_date'])

    PAGE_W, PAGE_H = A4

    fmt = LABEL_FORMATS.get(int(label_format or 0), LABEL_FORMATS[DEFAULT_LABEL_FORMAT])
    LBL_W, LBL_H = (v * mm for v in fmt['size'])
    COLS,  ROWS  = fmt['grid']
    SX,    SY    = (v * mm for v in fmt['margin'])
    GX,    GY    = (v * mm for v in fmt['gap'])

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    C_INK     = HexColor('#1A1714')
    C_BAR_TEXT = HexColor(bar_text_hex)
    C_MUTED  = HexColor('#9A8F80')
    C_BORDER = HexColor('#D8CFBF')
    C_ACCENT = HexColor(colour_hex)
    C_WHITE  = colors.white

    # Extract last 3 digits of invoice number for colour bar
    inv_suffix = re.sub(r'\D', '', meta['job_number'])[-3:] if meta['job_number'] else ''

    def draw_label(x, y, item):
        w, h  = LBL_W, LBL_H
        pad   = 0.18 * cm
        bar_w = w * 0.28

        # Colour bar
        c.setFillColor(C_ACCENT)
        c.roundRect(x, y, bar_w, h, 4, fill=1, stroke=0)
        c.rect(x + bar_w - 4, y, 6, h, fill=1, stroke=0)

        # White area
        c.setFillColor(C_WHITE)
        c.roundRect(x + bar_w, y, w - bar_w, h, 4, fill=1, stroke=0)
        c.rect(x + bar_w, y, 4, h, fill=1, stroke=0)

        # Border
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.7)
        c.roundRect(x, y, w, h, 4, fill=0, stroke=1)

        # Invoice suffix — rotated 90° clockwise on colour bar
        if inv_suffix:
            # Fill bar width (rotated, so fit against bar height h)
            inv_size = 48
            c.setFont('Helvetica-Bold', inv_size)
            while c.stringWidth(inv_suffix, 'Helvetica-Bold', inv_size) > h - 4 * mm and inv_size > 8:
                inv_size -= 1
            # Also constrain to bar_w so it doesn't overflow horizontally when rotated
            while c.stringWidth(inv_suffix, 'Helvetica-Bold', inv_size) * 0.6 > bar_w - 2 and inv_size > 8:
                inv_size -= 1
            inv_w = c.stringWidth(inv_suffix, 'Helvetica-Bold', inv_size)
            c.setFillColor(C_BAR_TEXT)
            c.saveState()
            c.translate(x + bar_w / 2, y + h / 2)
            c.rotate(-90)
            c.drawString(-inv_w / 2, -inv_size * 0.35, inv_suffix)
            c.restoreState()

        # Right panel
        rx  = x + bar_w + pad
        rxe = x + w - pad
        rw  = rxe - rx

        # ── Layout ───────────────────────────────────────────────
        # Item number sits under a divider at the top; the date and address
        # form one block, centred in the white space below it. Both are sized
        # as large as the panel allows rather than to fixed points, so a short
        # address on a wide label uses the space it has.

        # Divider and item number — fixed at the top
        div_y = y + h - pad - 10 * 1.2
        c.setStrokeColor(C_BORDER); c.setLineWidth(0.3)
        c.line(rx, div_y, rxe, div_y)

        ID_FONT = 9
        ID_Y    = div_y - ID_FONT * 1.4 - 2
        id_txt  = f'#{item["serial"]}'
        id_w    = c.stringWidth(id_txt, 'Helvetica-Bold', ID_FONT)
        c.setFillColor(C_MUTED); c.setFont('Helvetica-Bold', ID_FONT)
        c.drawString(rx + (rw - id_w) / 2, ID_Y, id_txt)

        # The zone the date/address block is centred in: from the bottom pad up
        # to just under the item number.
        zone_bottom = y + pad
        zone_top    = ID_Y - 3
        zone_h      = zone_top - zone_bottom

        addr = label_address(meta['address'])

        def fit(text, font, start_sz, min_sz, max_w):
            """Largest size at or below start_sz whose text fits max_w."""
            sz = start_sz
            while sz > min_sz and c.stringWidth(text, font, sz) > max_w:
                sz -= 0.25
            return sz

        def wrap_to(text, font, sz, max_w, max_lines=2):
            """Greedy wrap. Returns None if it can't fit in max_lines."""
            words, lines, cur = text.split(), [], ''
            for word in words:
                trial = f'{cur} {word}'.strip()
                if c.stringWidth(trial, font, sz) <= max_w:
                    cur = trial
                else:
                    if cur:
                        lines.append(cur)
                    cur = word
                    if c.stringWidth(cur, font, sz) > max_w:
                        return None          # a single word too wide
                    if len(lines) > max_lines:
                        return None
            if cur:
                lines.append(cur)
            return lines if len(lines) <= max_lines else None

        date_sz = fit(date_txt, 'Helvetica-Bold', 40, 7, rw)

        # Address: take the largest size that fits on one or two lines. Two
        # large lines beat one small one on a label read at arm's length.
        addr_lines, addr_sz = [], 0
        if addr:
            addr_sz = min(date_sz * 0.72, 20)
            while addr_sz > 6:
                got = wrap_to(addr, 'Helvetica', addr_sz, rw)
                if got:
                    addr_lines = got
                    break
                addr_sz -= 0.25
            if not addr_lines:               # nothing fit — fall back to truncation
                addr_sz = 6
                trimmed = addr
                while trimmed and c.stringWidth(trimmed, 'Helvetica', addr_sz) > rw:
                    trimmed = trimmed[:-1]
                addr_lines = [trimmed[:-1] + '…'] if trimmed != addr else [trimmed]

        GAP = 3
        def block_h(d, a, n):
            return d * 1.05 + (GAP + n * a * 1.15 if n else 0)

        # Shrink together if the block is taller than the space available
        while block_h(date_sz, addr_sz, len(addr_lines)) > zone_h and date_sz > 7:
            date_sz -= 0.25
            if addr_lines:
                addr_sz = min(addr_sz, date_sz * 0.72)
                got = wrap_to(addr, 'Helvetica', addr_sz, rw)
                if got:
                    addr_lines = got

        total_h = block_h(date_sz, addr_sz, len(addr_lines))
        block_y = zone_bottom + (zone_h - total_h) / 2      # vertically centred

        c.setFillColor(C_INK)
        # Address lines sit at the bottom of the block, date above them
        for i, line in enumerate(reversed(addr_lines)):
            c.setFont('Helvetica', addr_sz)
            lw = c.stringWidth(line, 'Helvetica', addr_sz)
            c.drawString(rx + (rw - lw) / 2, block_y + i * addr_sz * 1.15, line)

        date_y = block_y + (len(addr_lines) * addr_sz * 1.15 + GAP if addr_lines else 0)
        c.setFont('Helvetica-Bold', date_sz)
        date_w = c.stringWidth(date_txt, 'Helvetica-Bold', date_sz)
        c.drawString(rx + (rw - date_w) / 2, date_y, date_txt)

        # Room name removed — left blank for stylist to write manually

    # Paginate
    per_page = COLS * ROWS
    total    = len(items)
    pages    = (total + per_page - 1) // per_page

    for pg in range(pages):
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        for idx in range(per_page):
            item_idx = pg * per_page + idx
            if item_idx >= total: break
            col = idx % COLS
            row = ROWS - 1 - (idx // COLS)
            draw_label(
                SX + col * (LBL_W + GX),
                SY + row * (LBL_H + GY),
                items[item_idx]
            )

        if pg < pages - 1:
            c.showPage()


    c.save()
    buffer.seek(0)
    return buffer.getvalue()



# ════════════════════════════════════════════════
# GENERATE CHECKLIST PDF
# ════════════════════════════════════════════════
def generate_checklist(meta, items):
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin    = 1.2 * cm,
        bottomMargin = 1.2 * cm,
        leftMargin   = 1.2 * cm,
        rightMargin  = 1.2 * cm,
    )

    C_INK    = HexColor('#1A1714')
    C_MUTED  = HexColor('#9A8F80')
    C_ACCENT = HexColor('#B8935A')
    C_LIGHT  = HexColor('#F5F0E8')
    C_BORDER = HexColor('#D8CFBF')

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('title',
        fontName='Helvetica-Bold', fontSize=20,
        textColor=C_INK, spaceAfter=2)
    sub_style = ParagraphStyle('sub',
        fontName='Helvetica', fontSize=11,
        textColor=C_MUTED, spaceAfter=2)
    meta_style = ParagraphStyle('meta',
        fontName='Helvetica-Bold', fontSize=10,
        textColor=C_INK, spaceAfter=0)
    cell_style = ParagraphStyle('cell',
        fontName='Helvetica', fontSize=10,
        textColor=C_INK, leading=13)
    hdr_style = ParagraphStyle('hdr',
        fontName='Helvetica-Bold', fontSize=10,
        textColor=colors.white, alignment=TA_CENTER)
    hdr_small_style = ParagraphStyle('hdr_small',
        fontName='Helvetica-Bold', fontSize=9,
        textColor=colors.white, alignment=TA_CENTER)

    story = []

    # ── Header ──
    story.append(Paragraph('LUMA <font color="#B8935A">Design</font> Co', title_style))
    story.append(Spacer(1, 14))
    story.append(Paragraph('Warehouse Packing Checklist', sub_style))
    story.append(Spacer(1, 8))

    # ── Header block: meta left, sign-off fields right ──
    inv_suffix = re.sub(r'\D', '', meta['job_number'])[-3:] if meta['job_number'] else meta['job_number']

    sign_style = ParagraphStyle('sign',
        fontName='Helvetica-Bold', fontSize=10,
        textColor=C_INK, spaceAfter=0)
    line_style = ParagraphStyle('line',
        fontName='Helvetica', fontSize=10,
        textColor=C_MUTED, spaceAfter=0)

    # Left: job details stacked
    owner_line = f'<b>Job Owner:</b> {meta.get("job_owner", "")}' if meta.get("job_owner") else '<b>Job Owner:</b> —'
    left_data = [
        [Paragraph(f'<b>Job Ref:</b> {inv_suffix}', meta_style)],
        [Paragraph(f'<b>Address:</b> {meta["address"]}', meta_style)],
        [Paragraph(f'<b>Installation Date:</b> {meta["stage_date"]}', meta_style)],
        [Paragraph(f'<b>Total Items:</b> {len(items)}', meta_style)],
        [Paragraph(owner_line, meta_style)],
    ]
    left_table = Table(left_data, colWidths=[10.5*cm])
    left_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), C_LIGHT),
        ('BOX',          (0,0), (-1,-1), 0.5, C_BORDER),
        ('TOPPADDING',   (0,0), (-1,-1), 7),
        ('BOTTOMPADDING',(0,0), (-1,-1), 7),
        ('LEFTPADDING',  (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
    ]))

    # Right: simple two-line sign-off
    right_data = [
        [Paragraph('<b>Job Owner:</b>', sign_style)],
        [Paragraph('<b>Transport Lead:</b>', sign_style)],
    ]
    right_table = Table(right_data, colWidths=[8.2*cm])
    right_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), colors.white),
        ('BOX',          (0,0), (-1,-1), 0.5, C_BORDER),
        ('TOPPADDING',   (0,0), (-1,-1), 9),
        ('BOTTOMPADDING',(0,0), (-1,-1), 9),
        ('LEFTPADDING',  (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW',    (0,0), (-1,0), 0.5, C_BORDER),
    ]))

    # Combine left and right side by side
    header_row = [[left_table, right_table]]
    header_table = Table(header_row, colWidths=[10.5*cm, 8.2*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING',  (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING',   (0,0), (-1,-1), 0),
        ('BOTTOMPADDING',(0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    # ── Table — grouped by room section headers ──
    # 4 columns: # | Description | Notes | Packed | Returned
    col_widths = [2.6*cm, 5.8*cm, 6.4*cm, 1.8*cm, 1.9*cm]  # total ~18.5cm

    hdr_two_line = ParagraphStyle('hdr_two_line',
        fontName='Helvetica-Bold', fontSize=8,
        textColor=colors.white, alignment=TA_CENTER, leading=11)

    headers = [
        Paragraph('#', hdr_style),
        Paragraph('Item', hdr_style),
        Paragraph('Description', hdr_style),
        Paragraph('Packed<br/><font size="7">(truck)</font>', hdr_two_line),
        Paragraph('Returned<br/><font size="7">(warehouse)</font>', hdr_two_line),
    ]

    # Group items by room preserving order
    from itertools import groupby as _groupby
    rows = [headers]
    style_cmds = [
        # Header row
        ('BACKGROUND',   (0,0), (-1,0), C_INK),
        ('TEXTCOLOR',    (0,0), (-1,0), colors.white),
        ('FONTNAME',     (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0,0), (-1,0), 10),
        ('TOPPADDING',   (0,0), (-1,0), 9),
        ('BOTTOMPADDING',(0,0), (-1,0), 9),
        ('LEFTPADDING',  (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN',        (0,0), (0,-1), 'CENTER'),
        ('ALIGN',        (3,0), (4,-1), 'CENTER'),
        ('GRID',         (0,0), (-1,-1), 0.4, C_BORDER),
        ('LINEBELOW',    (0,0), (-1,0), 1.0, C_INK),
    ]

    room_section_style = ParagraphStyle('room_section',
        fontName='Helvetica-Bold', fontSize=11,
        textColor=colors.white)

    data_row_idx = 1  # track row index for styling (1-based, row 0 = header)

    for room, group in _groupby([i for i in items if not i.get('is_extra')], key=lambda x: x['room']):
        group_items = list(group)

        # Room section header row — spans all columns
        section_row = [
            Paragraph(room.upper(), room_section_style),
            '', '', '', ''
        ]
        rows.append(section_row)
        style_cmds += [
            ('BACKGROUND',   (0, data_row_idx), (-1, data_row_idx), C_ACCENT),
            ('SPAN',         (0, data_row_idx), (-1, data_row_idx)),
            ('TOPPADDING',   (0, data_row_idx), (-1, data_row_idx), 7),
            ('BOTTOMPADDING',(0, data_row_idx), (-1, data_row_idx), 7),
            ('LINEABOVE',    (0, data_row_idx), (-1, data_row_idx), 1.0, C_ACCENT),
        ]
        data_row_idx += 1

        # Group consecutive identical descriptions into one row
        grouped_items = []
        i = 0
        while i < len(group_items):
            item = group_items[i]
            desc = item.get('description', '')
            # Count consecutive items with same description
            j = i + 1
            while j < len(group_items) and group_items[j].get('description', '') == desc:
                j += 1
            count = j - i
            first_serial = item['serial']
            last_serial  = group_items[j-1]['serial']
            grouped_items.append({
                'count':        count,
                'description':  desc,
                'first_serial': first_serial,
                'last_serial':  last_serial,
            })
            i = j

        for i, grp in enumerate(grouped_items):
            bg = colors.white if i % 2 == 0 else C_LIGHT
            # Serial display: single item shows #001, multiple shows #001–#006
            if grp['count'] == 1:
                serial_txt = f'<b>#{grp["first_serial"]}</b>'
            else:
                serial_txt = f'<b>#{grp["first_serial"]}–#{grp["last_serial"]}</b>'
            # Description: prefix quantity if more than one
            if grp['count'] > 1:
                item_txt = f'{grp["count"]}×  {grp["description"]}'
            else:
                item_txt = grp['description']

            # Auto-size font so serial always fits on one line
            # col width = 2.6cm, minus padding = ~2.2cm usable
            serial_col_w = 2.2 * cm
            serial_fs = 11
            from reportlab.pdfbase.pdfmetrics import stringWidth as _sw
            _raw = serial_txt.replace('<b>','').replace('</b>','')
            while _sw(_raw, 'Helvetica-Bold', serial_fs) > serial_col_w and serial_fs > 7:
                serial_fs -= 0.5

            serial_style = ParagraphStyle('num',
                fontName='Helvetica-Bold', fontSize=serial_fs,
                textColor=C_ACCENT, alignment=TA_CENTER, leading=serial_fs * 1.2)

            rows.append([
                Paragraph(serial_txt, serial_style),
                Paragraph(item_txt, cell_style),
                Paragraph('', cell_style),
                Paragraph('', cell_style),
                Paragraph('', cell_style),
            ])
            style_cmds += [
                ('BACKGROUND',   (0, data_row_idx), (-1, data_row_idx), bg),
                ('FONTNAME',     (0, data_row_idx), (-1, data_row_idx), 'Helvetica'),
                ('FONTSIZE',     (0, data_row_idx), (-1, data_row_idx), 10),
                ('TOPPADDING',   (0, data_row_idx), (-1, data_row_idx), 7),
                ('BOTTOMPADDING',(0, data_row_idx), (-1, data_row_idx), 7),
            ]
            data_row_idx += 1

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    # ── Extras section ──
    extras = [item for item in items if item.get('is_extra')]
    if extras:
        story.append(Spacer(1, 14))
        extras_rows = [headers]
        extras_style_cmds = [
            ('BACKGROUND',   (0,0), (-1,0), C_INK),
            ('TEXTCOLOR',    (0,0), (-1,0), colors.white),
            ('FONTNAME',     (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',     (0,0), (-1,0), 10),
            ('TOPPADDING',   (0,0), (-1,0), 9),
            ('BOTTOMPADDING',(0,0), (-1,0), 9),
            ('LEFTPADDING',  (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN',        (0,0), (0,-1), 'CENTER'),
            ('ALIGN',        (3,0), (4,-1), 'CENTER'),
            ('GRID',         (0,0), (-1,-1), 0.4, C_BORDER),
            ('LINEBELOW',    (0,0), (-1,0), 1.0, C_INK),
        ]

        # Section header
        extras_rows.append([Paragraph('EXTRAS', room_section_style), '', '', '', ''])
        extras_style_cmds += [
            ('BACKGROUND',   (0,1), (-1,1), C_ACCENT),
            ('SPAN',         (0,1), (-1,1)),
            ('TOPPADDING',   (0,1), (-1,1), 7),
            ('BOTTOMPADDING',(0,1), (-1,1), 7),
        ]
        row_i = 2
        for i, item in enumerate(extras):
            bg = colors.white if i % 2 == 0 else C_LIGHT
            extras_rows.append([
                Paragraph(f'<b>{item["serial"]}</b>', ParagraphStyle('num2',
                    fontName='Helvetica-Bold', fontSize=10,
                    textColor=C_ACCENT, alignment=TA_CENTER)),
                Paragraph('', cell_style),
                Paragraph('', cell_style),
                Paragraph('', cell_style),
                Paragraph('', cell_style),
            ])
            extras_style_cmds += [
                ('BACKGROUND',   (0, row_i), (-1, row_i), bg),
                ('FONTNAME',     (0, row_i), (-1, row_i), 'Helvetica'),
                ('FONTSIZE',     (0, row_i), (-1, row_i), 10),
                ('TOPPADDING',   (0, row_i), (-1, row_i), 7),
                ('BOTTOMPADDING',(0, row_i), (-1, row_i), 7),
            ]
            row_i += 1

        extras_table = Table(extras_rows, colWidths=col_widths, repeatRows=1)
        extras_table.setStyle(TableStyle(extras_style_cmds))
        story.append(extras_table)

    # ── Footer ──
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        'LUMA Design Co  ·  lumadesignco.com.au  ·  Warehouse Automation',
        ParagraphStyle('footer', fontName='Helvetica', fontSize=7,
                       textColor=C_MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def _fetch_thumbnail(url, cache):
    """Download an item photo for embedding, or return None.

    Never raises: a missing or slow image must not stop the summary from
    being produced — the stylist is usually standing in a house waiting
    for it."""
    if not url:
        return None
    if url in cache:
        return cache[url]
    data = None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'LUMA/1.0'})
        with urllib.request.urlopen(req, timeout=4) as r:
            raw = r.read(3_000_000)          # ignore anything absurdly large
        if raw:
            data = BytesIO(raw)
    except Exception:
        data = None
    cache[url] = data
    return data


def generate_job_summary(job, items, room_notes=None, photos_by_item=None):
    """Export a live snapshot of a job from the stylist page.

    Laid out to match the PACKING SLIP the job arrived as, so the crew is
    reading a familiar document: same title block, same Description /
    Quantity table, rooms in caps with their items beneath. What it adds
    is everything the original can't carry — every note (job, room and
    item), picked status, transfer markings, and a thumbnail of each item
    that has a photo.
    """
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, Image, KeepTogether)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader

    room_notes     = room_notes or {}
    photos_by_item = photos_by_item or {}
    cache          = {}
    buffer         = BytesIO()

    PAGE_W, PAGE_H = A4
    L_MARGIN = 18 * mm

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=L_MARGIN, rightMargin=L_MARGIN,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"LUMA Job Summary {job.get('job_number') or ''}",
    )

    INK    = HexColor('#1A1714')
    MUTED  = HexColor('#6B625A')
    RULE   = HexColor('#C9C2B8')
    GREEN  = HexColor('#2E7D32')

    title_style = ParagraphStyle('t', fontName='Helvetica', fontSize=22,
                                 textColor=INK, leading=26)
    lbl_style   = ParagraphStyle('l', fontName='Helvetica-Bold', fontSize=7.5,
                                 textColor=INK, leading=10)
    val_style   = ParagraphStyle('v', fontName='Helvetica', fontSize=8.5,
                                 textColor=INK, leading=11)
    addr_style  = ParagraphStyle('a', fontName='Helvetica', fontSize=8.5,
                                 textColor=INK, leading=12)
    room_style  = ParagraphStyle('r', fontName='Helvetica', fontSize=9.5,
                                 textColor=INK, leading=13)
    item_style  = ParagraphStyle('i', fontName='Helvetica', fontSize=8.5,
                                 textColor=INK, leading=12)
    note_style  = ParagraphStyle('n', fontName='Helvetica-Oblique', fontSize=7.5,
                                 textColor=MUTED, leading=10, leftIndent=6)
    qty_style   = ParagraphStyle('q', fontName='Helvetica', fontSize=8.5,
                                 textColor=INK, leading=11, alignment=2)
    head_style  = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=7.5,
                                 textColor=INK, leading=10)
    head_r      = ParagraphStyle('hr', parent=head_style, alignment=2)

    story = []

    # ── Title block, mirroring the packing slip ──
    logo_cell = ''
    try:
        logo_path = os.path.join(app.static_folder or 'static', 'luma-logo.png')
        if os.path.exists(logo_path):
            ir = ImageReader(logo_path)
            iw, ih = ir.getSize()
            # Fit the mark inside a small box — scaling by width alone made a
            # tall logo fill a third of the page.
            s = min(38 * mm / iw, 20 * mm / ih) if iw and ih else 1
            logo_cell = Image(logo_path, width=iw * s, height=ih * s)
            logo_cell.hAlign = 'RIGHT'
    except Exception:
        logo_cell = ''

    story.append(Table(
        [[Paragraph('JOB SUMMARY', title_style), logo_cell]],
        colWidths=[105 * mm, 69 * mm],
        style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                          ('LEFTPADDING', (0, 0), (-1, -1), 0),
                          ('RIGHTPADDING', (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 10 * mm))

    address = job.get('address') or ''
    meta_left = Table(
        [[Paragraph(address, addr_style)]],
        colWidths=[62 * mm],
        style=TableStyle([('BOX', (0, 0), (-1, -1), 0.6, RULE),
                          ('TOPPADDING', (0, 0), (-1, -1), 7),
                          ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                          ('LEFTPADDING', (0, 0), (-1, -1), 8),
                          ('RIGHTPADDING', (0, 0), (-1, -1), 8)]))

    stage = job.get('runsheet_date') or job.get('stage_date') or ''
    stage_txt = '—'
    if stage:
        stage_txt = stage
        for fmt in ('%Y-%m-%d', '%d %B %Y', '%d %b %Y'):
            try:
                dt = datetime.strptime(str(stage).strip()[:10], fmt)
                stage_txt = f"{dt.day} {dt.strftime('%B %Y')}"   # '7 May 2026'
                break
            except ValueError:
                continue

    meta_mid = [
        [Paragraph('Install Date', lbl_style)],
        [Paragraph(stage_txt, val_style)],
        [Spacer(1, 5)],
        [Paragraph('Invoice Number', lbl_style)],
        [Paragraph(job.get('job_number') or '—', val_style)],
        [Spacer(1, 5)],
        [Paragraph('Reference', lbl_style)],
        [Paragraph(job.get('job_ref') or '—', val_style)],
    ]
    meta_right = [
        [Paragraph('LUMA Design Co Pty Ltd', val_style)],
        [Paragraph('Unit 2 23 Perivale St', val_style)],
        [Paragraph('DARRA QLD 4076', val_style)],
        [Paragraph('AUSTRALIA', val_style)],
        [Spacer(1, 6)],
        [Paragraph('ABN', lbl_style)],
        [Paragraph('96 675 056 201', val_style)],
    ]
    inner = TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                        ('TOPPADDING', (0, 0), (-1, -1), 0),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 1)])
    story.append(Table(
        [[meta_left,
          Table(meta_mid, colWidths=[46 * mm], style=inner),
          Table(meta_right, colWidths=[56 * mm], style=inner)]],
        colWidths=[64 * mm, 50 * mm, 60 * mm],
        style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                          ('LEFTPADDING', (0, 0), (-1, -1), 0),
                          ('RIGHTPADDING', (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 12 * mm))

    # Job-level notes sit above the table, where they can't be missed
    job_note = (job.get('notes') or '').strip()
    if job_note:
        story.append(Table(
            [[Paragraph('<b>Job notes</b><br/>' + job_note.replace('\n', '<br/>'), item_style)]],
            colWidths=[174 * mm],
            style=TableStyle([('BOX', (0, 0), (-1, -1), 0.6, RULE),
                              ('BACKGROUND', (0, 0), (-1, -1), HexColor('#F7F4EF')),
                              ('TOPPADDING', (0, 0), (-1, -1), 8),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                              ('LEFTPADDING', (0, 0), (-1, -1), 9),
                              ('RIGHTPADDING', (0, 0), (-1, -1), 9)])))
        story.append(Spacer(1, 7 * mm))

    # ── Description / Quantity table ──
    PHOTO_W  = 20 * mm
    QTY_W    = 22 * mm
    DESC_W   = 174 * mm - PHOTO_W - QTY_W
    col_w    = [PHOTO_W, DESC_W, QTY_W]

    rows   = [['', Paragraph('Description', head_style), Paragraph('Quantity', head_r)]]
    styles = [
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, INK),
        ('VALIGN',    (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
    ]

    # Preserve the order rooms appear in, like the original document
    room_order, by_room = [], {}
    for it in items:
        room = (it.get('room') or 'UNASSIGNED').strip() or 'UNASSIGNED'
        if room not in by_room:
            by_room[room] = []
            room_order.append(room)
        by_room[room].append(it)

    for room in room_order:
        room_items = by_room[room]
        r = len(rows)
        rows.append([
            '',
            Paragraph(f'<b>{room.upper()}</b>', room_style),
            Paragraph('%.2f' % 1, qty_style),
        ])
        styles.append(('LINEABOVE', (0, r), (-1, r), 0.5, RULE))
        styles.append(('TOPPADDING', (0, r), (-1, r), 8))

        for note in room_notes.get(room, []):
            if not note:
                continue
            rows.append(['', Paragraph('✎ ' + note, note_style), ''])

        # Collapse repeats: eight identical dining chairs read as one line,
        # "8 × Dining Chair", not eight. Transfer markings still split a group,
        # since those genuinely describe different items.
        groups, index = [], {}
        for it in room_items:
            key = ((it.get('description') or '').strip().lower(),
                   bool(it.get('is_transfer_item')),
                   bool(it.get('not_transferring')))
            if key in index:
                groups[index[key]].append(it)
            else:
                index[key] = len(groups)
                groups.append([it])

        for members in groups:
            it    = members[0]
            count = len(members)
            desc  = (it.get('description') or '').strip() or '—'
            if count > 1:
                desc = f'{count} × {desc}'

            marks = []
            picked_n = sum(1 for m in members if m.get('picked'))
            if picked_n == count:
                marks.append('<font color="#2E7D32">✓ picked</font>')
            elif picked_n:
                # Never imply the whole group is done when only part of it is
                marks.append(f'<font color="#2E7D32">{picked_n} of {count} picked</font>')
            if it.get('is_transfer_item'):
                marks.append('<font color="#7A4A00">transfer</font>')
            if it.get('not_transferring'):
                marks.append('<font color="#7A4A00">not transferring</font>')

            serials = [str(m.get('serial')) for m in members if m.get('serial')]
            tag = ''
            if serials:
                if count > 1:
                    tag = f'#{serials[0]}–{serials[-1]}' if count > 2 else f'#{serials[0]}, {serials[-1]}'
                else:
                    tag = f'#{serials[0]}'

            line = desc
            if tag:
                line = f'<font color="#6B625A" size="7">{tag}</font>  {line}'
            if marks:
                line += '  <font size="7">(' + ' · '.join(marks) + ')</font>'

            block = [Paragraph(line, item_style)]
            # Keep every note in the group, labelled when they differ
            seen_notes = []
            for m in members:
                note = (m.get('notes') or '').strip()
                if note and note not in seen_notes:
                    seen_notes.append(note)
                    prefix = f'#{m.get("serial")}: ' if count > 1 and m.get('serial') else ''
                    block.append(Paragraph(prefix + note.replace('\n', '<br/>'), note_style))

            thumb = ''
            urls = []
            for m in members:
                if m.get('photo_url'):
                    urls.append(m['photo_url'])
                urls += photos_by_item.get(m.get('id')) or []
            for u in urls[:1]:
                data = _fetch_thumbnail(u, cache)
                if not data:
                    continue
                try:
                    data.seek(0)
                    iw, ih = ImageReader(data).getSize()
                    # Fit inside the box without distorting: scale by whichever
                    # side runs out of room first.
                    box_w, box_h = 16 * mm, 18 * mm
                    scale = min(box_w / iw, box_h / ih) if iw and ih else 1
                    data.seek(0)
                    thumb = Image(data, width=iw * scale, height=ih * scale)
                    thumb.hAlign = 'LEFT'
                except Exception:
                    thumb = ''

            rows.append([thumb, block, ''])

    story.append(Table(rows, colWidths=col_w, repeatRows=1, style=TableStyle(styles)))

    def _footer(canv, _doc):
        canv.saveState()
        canv.setFont('Helvetica', 7)
        canv.setFillColor(MUTED)
        ref = job.get('job_ref') or job.get('job_number') or ''
        canv.drawString(L_MARGIN, 10 * mm, f'LUMA Design Co — {ref}')
        canv.drawRightString(PAGE_W - L_MARGIN, 10 * mm, f'Page {canv.getPageNumber()}')
        canv.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer.getvalue()


@app.route('/', methods=['GET'])
def home():
    with open('templates/home.html', 'r') as f:
        return f.read()

@app.route('/labels', methods=['GET'])
def index():
    with open('templates/index.html', 'r') as f:
        return f.read()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'LUMA Label Generator'})


@app.route('/checklist', methods=['POST'])
def checklist():
    try:
        data        = request.get_json()
        pdf_base64  = data.get('pdfBase64')
        file_name   = data.get('fileName', 'packing_list.pdf')

        if not pdf_base64:
            return jsonify({'success': False, 'error': 'No pdfBase64 provided'}), 400

        pdf_bytes       = base64.b64decode(pdf_base64)
        install_date    = data.get('installDate')
        install_address = data.get('installAddress', '').strip()
        job_owner       = data.get('jobOwner', '')
        label_format    = int(data.get('labelFormat', DEFAULT_LABEL_FORMAT))  # 16 or 18 per page
        meta, items     = parse_packing_list(pdf_bytes)
        meta['job_owner'] = job_owner
        if install_address:
            meta['address'] = install_address

        if not items:
            return jsonify({'success': False, 'error': 'No items found'}), 400

        if install_date:
            try:
                dt = datetime.strptime(install_date, '%Y-%m-%d')
                meta['stage_date'] = dt.strftime('%-d %B %Y')
            except:
                pass

        checklist_bytes    = generate_checklist(meta, items)
        checklist_filename = f'LUMA_Checklist_{meta["job_number"]}_{format_date(meta["stage_date"]).replace(" ", "")}.pdf'

        return Response(
            checklist_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{checklist_filename}"',
                'X-Job-Number': meta['job_number'],
                'X-Item-Count': str(len(items)),
            }
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/generate', methods=['POST'])
def generate():
    try:
        data        = request.get_json()
        pdf_base64  = data.get('pdfBase64')
        file_name   = data.get('fileName', 'packing_list.pdf')

        if not pdf_base64:
            return jsonify({'success': False, 'error': 'No pdfBase64 provided'}), 400

        pdf_bytes       = base64.b64decode(pdf_base64)
        install_date    = data.get('installDate')
        install_address = data.get('installAddress', '').strip()
        job_owner       = data.get('jobOwner', '')
        label_format    = int(data.get('labelFormat', DEFAULT_LABEL_FORMAT))  # 16 or 18 per page
        colour_name     = data.get('colourName')  # manual colour choice, or None for Auto
        is_transfer     = bool(data.get('isTransfer', False))
        transfer_from_job_id = data.get('transferFromJobId') or None
        meta, items     = parse_packing_list(pdf_bytes)
        if install_address:
            meta['address'] = install_address

        if not items:
            return jsonify({'success': False, 'error': 'No items found in packing list'}), 400

        # Override stage date with user-entered install date if provided
        if install_date:
            try:
                dt = datetime.strptime(install_date, '%Y-%m-%d')
                meta['stage_date'] = dt.strftime('%-d %B %Y')
            except:
                pass  # keep whatever the parser found

        # If this exact job already exists (re-uploading the same packing
        # slip), keep its existing colour rather than picking a new one.
        # This must happen *before* get_next_colour() so the printed
        # labels and the saved database row always agree on the colour —
        # correcting it only in save_job_to_db (after labels are already
        # rendered) would let the PDF and the database disagree.
        if not colour_name:
            try:
                existing_job = sb_get('jobs', f'job_number=eq.{meta["job_number"]}')
                if existing_job and existing_job[0].get('colour'):
                    colour_name = existing_job[0]['colour']
            except Exception:
                pass  # fall through to normal auto-selection if this lookup fails

        colour         = get_next_colour(colour_name)
        pdf_bytes_out  = generate_labels(meta, items, colour, label_format)
        label_filename = f'LUMA_Labels_{meta["job_number"]}_{format_date(meta["stage_date"]).replace(" ", "")}.pdf'

        # Save job to database (non-blocking)
        save_job_to_db(meta, items, colour['name'], job_owner, is_transfer,
                       transfer_from_job_id, install_date_iso=install_date or None)

        # Send PDF directly to browser as a download — no third-party hosting needed
        job_ref = re.sub(r'\D', '', meta['job_number'])[-3:] if meta['job_number'] else '000'
        return Response(
            pdf_bytes_out,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{label_filename}"',
                'X-Job-Number':  meta['job_number'],
                'X-Job-Ref':     job_ref,
                'X-Item-Count':  str(len(items)),
                'X-Colour':      colour['name'],
                'X-Address':     meta['address'],
                'X-Stage-Date':  meta['stage_date'],
            }
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ════════════════════════════════════════════════
# JOB TRACKER ROUTES
# ════════════════════════════════════════════════

@app.route('/jobs', methods=['GET'])
def jobs_page():
    with open('templates/jobs.html', 'r') as f:
        return f.read()

@app.route('/catalogue', methods=['GET'])
def catalogue_page():
    with open('templates/catalogue.html', 'r') as f:
        return f.read()

@app.route('/today', methods=['GET'])
def today_page():
    with open('templates/today.html', 'r') as f:
        return f.read()

@app.route('/runsheet', methods=['GET'])
def runsheet_page():
    with open('templates/today.html', 'r') as f:
        return f.read()

@app.route('/scheduler', methods=['GET'])
def scheduler_page():
    with open('templates/runsheet.html', 'r') as f:
        return f.read()

@app.route('/damages', methods=['GET'])
def damages_page():
    with open('templates/damages.html', 'r') as f:
        return f.read()

@app.route('/damages/guide', methods=['GET'])
def damages_guide():
    with open('templates/damages_guide.html', 'r') as f:
        return f.read()

@app.route('/api/damages', methods=['GET'])
def api_damages_list():
    rows = sb_get('damage_reports', 'order=created_at.desc')
    return jsonify(rows or [])

DAMAGE_REPAIR_STATUSES = ['to_schedule', 'scheduled', 'fixed', 'discard']


@app.route('/api/damages', methods=['POST'])
def api_damages_create():
    data = request.get_json()
    result = sb_post('damage_reports', {
        'location':          data.get('location'),
        'damage_type':       data.get('damage_type'),
        'furniture':         data.get('furniture'),
        'report_category':   data.get('report_category') or 'furniture',
        'property_element':  data.get('property_element'),
        'damage_origin':     data.get('damage_origin'),
        # Repair tracking: to_schedule / scheduled / fixed / discard.
        # New reports start as "to be scheduled" — a damage nobody has
        # triaged yet is exactly the thing that gets forgotten.
        'repair_status':     data.get('repair_status') or 'to_schedule',
        'job_id':            data.get('job_id') or None,
        'job_ref_snapshot':  data.get('job_ref_snapshot') or None,
        'photo_url':         data.get('photo_url') or None,
        'notes':             data.get('notes') or None,
    })
    return jsonify({'success': bool(result), 'report': result[0] if result else None})

@app.route('/api/damages/<report_id>', methods=['PATCH'])
def api_damages_update(report_id):
    data = request.get_json()
    payload = {}
    if 'location'         in data: payload['location']         = data['location']
    if 'damage_type'      in data: payload['damage_type']      = data['damage_type']
    if 'furniture'        in data: payload['furniture']        = data['furniture'] or None
    if 'report_category'  in data: payload['report_category']  = data['report_category']
    if 'property_element' in data: payload['property_element'] = data['property_element'] or None
    if 'damage_origin'   in data: payload['damage_origin']    = data['damage_origin'] or None
    if 'repair_status'    in data:
        status = data['repair_status'] or 'to_schedule'
        if status not in DAMAGE_REPAIR_STATUSES:
            return jsonify({'success': False, 'error': f'Unknown status: {status}'}), 400
        payload['repair_status'] = status
    if 'job_id'           in data: payload['job_id']           = data['job_id'] or None
    if 'job_ref_snapshot' in data: payload['job_ref_snapshot'] = data['job_ref_snapshot'] or None
    if 'photo_url'        in data: payload['photo_url']        = data['photo_url'] or None
    if 'notes'            in data: payload['notes']            = data['notes'] or None
    result = sb_patch('damage_reports', f'id=eq.{report_id}', payload)
    if result:
        return jsonify({'success': True, 'report': result[0]})
    err = sb_last_error()
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': False, 'error': 'That report no longer exists.'}), 409

@app.route('/api/damages/<report_id>', methods=['DELETE'])
def api_damages_delete(report_id):
    result = sb_delete('damage_reports', f'id=eq.{report_id}')
    return jsonify({'success': bool(result)})

@app.route('/stylist/<job_id>', methods=['GET'])
def stylist_page(job_id):
    with open('templates/stylist.html', 'r') as f:
        return f.read()

@app.route('/driver/<job_id>', methods=['GET'])
def driver_page(job_id):
    with open('templates/driver.html', 'r') as f:
        return f.read()

@app.route('/api/jobs', methods=['GET'])
def api_jobs():
    jobs = sb_get('jobs', 'order=created_at.desc')
    return jsonify(jobs)

@app.route('/api/jobs/<job_id>', methods=['GET'])
def api_job(job_id):
    job   = sb_get('jobs',  f'id=eq.{job_id}')
    items = sb_get('items', f'job_id=eq.{job_id}&order=serial.asc')
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    transferring_to = sb_get('jobs', f'transfer_from_job_id=eq.{job_id}')
    schedule = sb_get('job_schedule', f'job_id=eq.{job_id}&order=start_time.asc')
    return jsonify({'job': job[0], 'items': items,
                    'transferring_to': transferring_to,
                    'schedule': schedule or []})

@app.route('/api/jobs/<job_id>/room-notes', methods=['GET'])
def api_job_room_notes(job_id):
    """Stylist-only — room-level notes parsed from packing-slip bracket
    text. Deliberately not included in /api/jobs/<job_id> so the driver
    page has no code path to this data at all."""
    notes = sb_get('room_notes', f'job_id=eq.{job_id}')
    # Group into {room: [note, note, ...]} for easy lookup on the frontend
    by_room = {}
    for n in notes:
        by_room.setdefault(n['room'], []).append(n['note'])
    return jsonify(by_room)

@app.route('/api/jobs/<job_id>/access-notes', methods=['PATCH'])
def api_job_access_notes(job_id):
    """Property access notes — gate codes, lockbox, parking, where the key is.

    Lives on the job so every crew tile for that property shows the same thing,
    including the load crew the day before."""
    data  = request.get_json(silent=True) or {}
    notes = (data.get('access_notes') or '').strip() or None

    result = sb_patch('jobs', f'id=eq.{job_id}', {'access_notes': notes})
    if result:
        return jsonify({'success': True, 'job': result[0]})
    err = sb_last_error()
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': False, 'stale': True,
                    'error': 'That job no longer exists — the page will refresh.'}), 409


@app.route('/api/jobs/<job_id>/labels-pdf', methods=['GET'])
def api_job_labels_pdf(job_id):
    """Re-generate the Avery labels PDF from stored job data.
    Uses the job's current colour, stage_date, and items from the database."""
    job_rows = sb_get('jobs', f'id=eq.{job_id}')
    if not job_rows:
        return jsonify({'error': 'Job not found'}), 404
    job   = job_rows[0]
    items = sb_get('items', f'job_id=eq.{job_id}&order=serial.asc') or []

    meta = {
        'job_number':  job.get('job_number', ''),
        'job_ref':     job.get('job_ref', ''),
        'address':     job.get('address', ''),
        'stage_date':  job.get('runsheet_date') or job.get('stage_date', ''),
        'job_owner':   job.get('job_owner', ''),
    }

    colour_name = job.get('colour') or 'Teal'
    colour      = find_colour(colour_name)
    if not colour:
        # Don't silently print the wrong colour — the crew matches labels to
        # physical stock by colour, so a wrong one is worse than an odd one.
        print(f'[labels] unknown colour {colour_name!r} for job {job_id} — '
              f'falling back to {COLOURS[0]["name"]}', flush=True)
        colour = COLOURS[0]

    # Defaults to the current stock; ?format=18 still prints the old sheet.
    try:
        fmt = int(request.args.get('format', DEFAULT_LABEL_FORMAT))
    except (TypeError, ValueError):
        fmt = DEFAULT_LABEL_FORMAT
    pdf_bytes = generate_labels(meta, items, colour, fmt)
    filename  = f'LUMA_Labels_{meta["job_number"]}_{format_date(meta["stage_date"]).replace(" ", "")}.pdf'
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/api/jobs/<job_id>/summary-pdf', methods=['GET'])
def api_job_summary_pdf(job_id):
    """Stylist-only export — a PDF snapshot of the job's current live
    state (picked status, notes, transfer markings) as shown on
    /stylist/<id>. See generate_job_summary() for why this can't just
    reuse generate_checklist()."""
    job_rows = sb_get('jobs', f'id=eq.{job_id}')
    if not job_rows:
        return jsonify({'error': 'Job not found'}), 404
    job   = job_rows[0]
    items = sb_get('items', f'job_id=eq.{job_id}&order=serial.asc')

    notes = sb_get('room_notes', f'job_id=eq.{job_id}')
    room_notes = {}
    for n in notes:
        room_notes.setdefault(n['room'], []).append(n['note'])

    # One query for every photo on the job rather than one per item
    photos_by_item = {}
    ids = [i['id'] for i in (items or []) if i.get('id')]
    if ids:
        try:
            for p in sb_get('item_photos',
                            f"item_id=in.({','.join(ids)})&order=created_at.asc") or []:
                url = p.get('photo_url') or p.get('url')
                if url:
                    photos_by_item.setdefault(p['item_id'], []).append(url)
        except Exception:
            photos_by_item = {}

    pdf_bytes = generate_job_summary(job, items, room_notes, photos_by_item)
    ref = job.get('job_ref') or job.get('job_number') or job_id
    filename = f'LUMA_Job_Summary_{ref}.pdf'
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )

@app.route('/api/jobs/<job_id>/status', methods=['PATCH'])
def api_job_status(job_id):
    data    = request.get_json()
    status  = data['status']
    if status == 'returned':
        status = 'archived'
    payload = {'status': status}
    if 'truck' in data:
        new_truck = data['truck']
        payload['truck'] = new_truck
        # Sync the runsheet: update any job_schedule entries for this job
        # to use the newly selected vehicle, so the runsheet tile moves to
        # the correct column automatically when the driver picks a truck.
        # Only update entries that already have a vehicle assigned — unassigned
        # entries (vehicle=null) are left alone.
        if new_truck and new_truck in RUNSHEET_VEHICLES:
            existing_entries = sb_get('job_schedule', f'job_id=eq.{job_id}')
            for entry in (existing_entries or []):
                if entry.get('vehicle'):  # only update assigned entries
                    sb_patch('job_schedule', f'id=eq.{entry["id"]}',
                             {'vehicle': new_truck})
    result = sb_patch('jobs', f'id=eq.{job_id}', payload)
    return jsonify({'success': bool(result)})

WAREHOUSE_ADDRESS = '63 Westgate St, Wacol QLD'

# Rough bounding box for Australia incl. Tasmania — lat_min, lat_max, lng_min, lng_max
AU_BOUNDS = (-44.0, -9.0, 112.0, 154.5)


# Only these mean "this address will never resolve". Everything else —
# REQUEST_DENIED, OVER_QUERY_LIMIT, a timeout — is a problem with the setup
# or the moment, not the address, and must NOT blacklist the job.
GEOCODE_PERMANENT_FAILURES = {'ZERO_RESULTS', 'INVALID_REQUEST', 'OUT_OF_BOUNDS'}


def geocode_address(address):
    """Address -> (coords, status).

    coords is (lat, lng) or None; status is Google's status string (or a
    local marker) so the caller can tell a bad address from a bad key.
    Never raises: a map with fewer pins beats a page that won't load.
    """
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        return None, 'NO_API_KEY'
    if not (address or '').strip():
        return None, 'NO_ADDRESS'
    try:
        params = urllib.parse.urlencode({
            'address':    address,
            # region= is only a soft bias — Google still returned US matches
            # for addresses like "5 Kent Rd" with no suburb. components=
            # is a hard restriction: an address that isn't in Australia comes
            # back as ZERO_RESULTS instead of the wrong continent.
            'components': 'country:AU',
            'region':     'au',
            'key':        api_key,
        })
        url = f'https://maps.googleapis.com/maps/api/geocode/json?{params}'
        with urllib.request.urlopen(urllib.request.Request(url), timeout=8) as r:
            result = json.loads(r.read())
        status = result.get('status')
        if status != 'OK' or not result.get('results'):
            print(f'[GEO] {status} for {address!r} — {result.get("error_message", "")}')
            return None, status or 'UNKNOWN'
        loc = result['results'][0]['geometry']['location']
        lat, lng = float(loc['lat']), float(loc['lng'])
        # Second line of defence: never cache a point outside Australia, even
        # if the API returns one. A pin in the wrong hemisphere is worse than
        # no pin, because it silently rescales the whole map.
        if not (AU_BOUNDS[0] <= lat <= AU_BOUNDS[1] and AU_BOUNDS[2] <= lng <= AU_BOUNDS[3]):
            print(f'[GEO] rejected out-of-bounds result for {address!r}: {lat},{lng}')
            return None, 'OUT_OF_BOUNDS'
        return (lat, lng), 'OK'
    except Exception as e:
        print(f'[GEO] failed for {address!r}: {e}')
        return None, 'EXCEPTION'


# Geocoding runs inside a page load, so cap it: a first open of a busy day
# geocodes a handful, the rest resolve on the next open. Cached forever after.
GEOCODE_MAX_PER_REQUEST = 12

_warehouse_cache = {}


@app.route('/api/map/<date_str>', methods=['GET'])
def api_map_day(date_str):
    """Points for the map tab: the day's jobs plus the warehouse.

    Coordinates are cached on the job row, so Google is only called for
    addresses never resolved before.
    """
    entries = sb_get('job_schedule', f'date=eq.{date_str}') or []
    job_ids = list({e['job_id'] for e in entries if e.get('job_id')})

    jobs = []
    if job_ids:
        jobs = sb_get('jobs', f"id=in.({','.join(job_ids)})") or []
    jobs_by_id = {j['id']: j for j in jobs}

    teams = sb_get('day_teams', f'date=eq.{date_str}') or []
    team_by_id = {t['id']: t for t in teams}

    geocoded_now = 0
    last_error = None
    points = []
    seen = set()

    # Only where crews actually go to a property. A To Load tile is
    # warehouse work the day BEFORE an install, so mapping it put jobs on
    # days they don't belong to.
    MAP_TYPES = ('install', 'pickup')

    for e in entries:
        if (e.get('type') or 'install') not in MAP_TYPES:
            continue
        # Only work actually given to a crew. An unassigned tray tile isn't
        # somewhere anyone is going today, so it doesn't belong on the map.
        if not e.get('team_id') and not e.get('vehicle'):
            continue
        job = jobs_by_id.get(e.get('job_id'))
        if not job:
            continue
        # one pin per job per type, even if several crews attend
        key = (job['id'], e.get('type'))
        if key in seen:
            continue
        seen.add(key)

        lat, lng = job.get('latitude'), job.get('longitude')
        if (lat is None or lng is None) and not job.get('geocode_failed') \
                and geocoded_now < GEOCODE_MAX_PER_REQUEST:
            coords, status = geocode_address(job.get('address'))
            geocoded_now += 1
            if status != 'OK':
                last_error = status
            now = datetime.utcnow().isoformat() + 'Z'
            if coords:
                lat, lng = coords
                sb_patch('jobs', f"id=eq.{job['id']}",
                         {'latitude': lat, 'longitude': lng, 'geocoded_at': now,
                          'geocode_failed': False})
                job['latitude'], job['longitude'] = lat, lng
            elif status in GEOCODE_PERMANENT_FAILURES:
                # Genuinely unresolvable — stop retrying it every open.
                sb_patch('jobs', f"id=eq.{job['id']}",
                         {'geocode_failed': True, 'geocoded_at': now})
            # Any other status (bad key, quota, network) is left alone so it
            # retries once the underlying problem is fixed.

        if lat is None or lng is None:
            continue

        team = team_by_id.get(e.get('team_id')) or {}
        points.append({
            'job_id':  job['id'],
            'ref':     job.get('job_ref') or job.get('job_number') or '—',
            'address': job.get('address') or '',
            'type':    e.get('type') or 'install',
            'time':    e.get('start_time'),
            'crew':    team.get('name') or team.get('vehicle') or '',
            # Navigate files the ETA against the truck or the stylist
            'crew_function': team.get('function') or '',
            'lat':     lat,
            'lng':     lng,
        })

    # The warehouse is a fixed point; resolve it once per process.
    if not _warehouse_cache.get('coords'):
        wh_coords, wh_status = geocode_address(WAREHOUSE_ADDRESS)
        if wh_coords:
            _warehouse_cache['coords'] = wh_coords
        elif wh_status != 'OK':
            last_error = last_error or wh_status
    wh = _warehouse_cache.get('coords')

    return jsonify({
        'points': points,
        'warehouse': ({'address': WAREHOUSE_ADDRESS, 'lat': wh[0], 'lng': wh[1]}
                      if wh else None),
        'unmapped': sum(1 for e in entries
                        if (e.get('type') or 'install') in MAP_TYPES
                        and (e.get('team_id') or e.get('vehicle'))
                        and jobs_by_id.get(e.get('job_id'))
                        and jobs_by_id[e['job_id']].get('latitude') is None),
        'geocoding_available': bool(os.environ.get('GOOGLE_MAPS_API_KEY')),
        # Surfaced so a blank map can be diagnosed without server log access
        'geocode_error': last_error,
    })


@app.route('/api/stylists', methods=['GET'])
def api_stylists():
    """The stylist roster, so the jobs page picker and the label screen
    can't drift apart."""
    return jsonify({'stylists': RUNSHEET_STYLISTS})


@app.route('/api/jobs/<job_id>/notes', methods=['PATCH'])
def api_job_notes(job_id):
    data    = request.get_json()
    payload = {}
    if 'styling_notes'  in data: payload['styling_notes']  = data['styling_notes']
    if 'driver_notes'   in data: payload['driver_notes']   = data['driver_notes']
    if 'address'        in data: payload['address']        = data['address'] or None
    # Stylist can be (re)assigned from the jobs page, not just at creation.
    # Empty string clears it, which is how the badge disappears again.
    if 'job_owner'      in data: payload['job_owner']      = (data['job_owner'] or '').strip()
    if 'accessory_tubs' in data:
        v = data['accessory_tubs']
        payload['accessory_tubs'] = int(v) if v not in (None, '', 0) else None
    if 'cushion_bags' in data:
        v = data['cushion_bags']
        payload['cushion_bags'] = int(v) if v not in (None, '', 0) else None
    # Whether the driver has loaded the packed bags / tubs. These are single
    # rows on the driver page standing in for many small items.
    if 'cushion_bags_loaded' in data:
        payload['cushion_bags_loaded'] = bool(data['cushion_bags_loaded'])
    if 'accessory_tubs_loaded' in data:
        payload['accessory_tubs_loaded'] = bool(data['accessory_tubs_loaded'])
    result = sb_patch('jobs', f'id=eq.{job_id}', payload)
    return jsonify({'success': bool(result)})

def count_bedrooms(items):
    """Count distinct bedroom rooms from a job's item list.
    Matches: Master Bedroom, Bedroom 2, 2nd Bedroom, Bedroom, etc.
    Ignores: Living Room, Kitchen, Bathroom, Study, etc."""
    bedroom_rooms = {
        item['room'] for item in items
        if item.get('room') and re.search(r'\b(bedroom|master)\b', item['room'], re.I)
    }
    return len(bedroom_rooms)


def vehicles_for_job(items):
    """Suggest vehicle(s) based on bedroom count.
    Rules (guides only — always overridable by the team):
      1–2 bedrooms  → Nemo   (smallest truck)
      3 bedrooms    → Nigel  (mid-size)
      4 bedrooms    → Bruce  (biggest single truck)
      5+ bedrooms   → Nigel + Nemo  (two trucks share the load)
    Returns a list of vehicle name strings."""
    n = count_bedrooms(items)
    if n == 0:
        return []
    elif n <= 2:
        return ['Nemo']
    elif n == 3:
        return ['Nigel']
    elif n == 4:
        return ['Bruce']
    else:  # 5+
        return ['Nigel', 'Nemo']


def seed_two_day_schedule(job_id, main_date_str, main_type, items=None, forced_vehicles=None):
    """Seed the schedule for a job — DAY PLACEMENT ONLY (August 2026 simplification).

    Creates unscheduled entries (no vehicle, no team, no time, no duration) that
    appear in the runsheet's "Unscheduled" tray for the relevant day. The admin
    then manually assigns team/time/duration/notes by clicking the tray tile.

    - pickup  → one tile: PICKUP on main_date
    - install → two tiles: LOAD the business day before + INSTALL on main_date
    - to_load → two tiles: LOAD on main_date + INSTALL the next business day

    Weekend-aware in both directions. Existing UNSCHEDULED tiles are cleared and
    rebuilt; tiles that already have a crew or a time are moved to the new date
    with their crew, time and duration intact.

    `items` and `forced_vehicles` are accepted for backward compatibility with
    existing call sites but are no longer used to guess vehicles — vehicle
    assignment is always manual now."""
    from datetime import datetime as _dt, timedelta

    # Capture any Monday linkage before clearing, so re-seeding doesn't
    # orphan the item and cause the next pull to create a duplicate tile.
    monday_link = None
    prior = []
    try:
        prior = sb_get('job_schedule', f'job_id=eq.{job_id}') or []
        for p in prior:
            if p.get('monday_item_id'):
                monday_link = {'monday_item_id': p['monday_item_id'],
                               'monday_address': p.get('monday_address')}
                break
    except Exception:
        prior = []
        monday_link = None

    try:
        main_dt = _dt.strptime(main_date_str, '%Y-%m-%d')
    except ValueError:
        return

    def prev_business_day(dt):
        w = dt.weekday()
        if w == 0: return dt - timedelta(days=3)
        if w == 6: return dt - timedelta(days=2)
        return dt - timedelta(days=1)

    def next_business_day(dt):
        w = dt.weekday()
        if w == 4: return dt + timedelta(days=3)
        if w == 5: return dt + timedelta(days=2)
        return dt + timedelta(days=1)

    def make_entry(date_str, etype):
        row = {
            'job_id':     job_id,
            'date':       date_str,
            'vehicle':    None,
            'team_id':    None,
            'type':       etype,
            'start_time': None,
            'duration':   None,
            'notes':      None,
        }
        # Re-attach the Monday link to the INSTALL tile so future pulls
        # update this entry rather than adding another one.
        if monday_link and etype == 'install':
            row.update(monday_link)
        sb_post('job_schedule', row)

    # What this job should have on the runsheet after this save.
    if main_type == 'pickup':
        wanted = {'pickup': main_date_str}
    elif main_type == 'install':
        wanted = {'to_load': prev_business_day(main_dt).strftime('%Y-%m-%d'),
                  'install': main_date_str}
    elif main_type == 'to_load':
        wanted = {'to_load': main_date_str,
                  'install': next_business_day(main_dt).strftime('%Y-%m-%d')}
    else:
        wanted = {}

    # A tile that has a crew or a time on it represents real scheduling work —
    # often several crews on one job. Changing the date MOVES those tiles to
    # the new date instead of deleting them. Only untouched tray tiles are
    # cleared and rebuilt. (This function used to delete every row for the job
    # first, which silently wiped every crew assignment on a date change.)
    def is_placed(r):
        return bool(r.get('team_id') or r.get('start_time'))

    kept_types = set()
    moved      = 0
    for p in prior:
        etype = p.get('type')
        if is_placed(p) and etype in wanted:
            if p.get('date') != wanted[etype]:
                sb_patch('job_schedule', f'id=eq.{p["id"]}', {'date': wanted[etype]})
                moved += 1
            kept_types.add(etype)          # crews already assigned — don't re-seed
        elif not is_placed(p):
            sb_delete('job_schedule', f'id=eq.{p["id"]}')
        # A placed tile whose type is no longer wanted (e.g. the job changed
        # from pickup to install) is deliberately left alone rather than
        # deleted — losing assigned crew work is worse than a stale tile the
        # admin can remove.

    for etype, edate in wanted.items():
        if etype not in kept_types:
            make_entry(edate, etype)

    # Safety net: remove any UNPLACED tile for this job left on a date we no
    # longer want. The loop above already deletes the ones it can see, but if
    # that read failed (or a tile was created by another path), the job keeps
    # showing in the old day's tray after its date moved. Only tiles with no
    # crew and no time are touched, so assigned work is never lost this way.
    try:
        keep_dates = ','.join(sorted(set(wanted.values())))
        sb_delete('job_schedule',
                  f'job_id=eq.{job_id}&team_id=is.null&start_time=is.null'
                  f'&date=not.in.({keep_dates})')
    except Exception as e:
        print(f'[SEED] stale tray sweep failed for {job_id}: {e}')

    # Keep jobs.runsheet_date on the main date for backward compat
    sb_patch('jobs', f'id=eq.{job_id}', {
        'runsheet_date': main_date_str,
        'runsheet_type': main_type,
    })


def _set_job_runsheet_date(job_id, runsheet_date, runsheet_type):
    """Keep the job's own date in step with its schedule tiles.

    The seeding branches moved the tiles but never wrote the date back to the
    job, so the jobs page badge, the label PDF and the Monday comparison could
    all disagree with what the runsheet actually showed."""
    sb_patch('jobs', f'id=eq.{job_id}', {
        'runsheet_date': runsheet_date,
        'runsheet_type': runsheet_type,
    })
    # A held job that just got a date is back in play
    sb_patch('jobs', f'id=eq.{job_id}&status=eq.on_hold', {'status': 'ready'})


@app.route('/api/jobs/<job_id>/runsheet', methods=['PATCH'])
def api_job_runsheet(job_id):
    data          = request.get_json()
    runsheet_date = data.get('runsheet_date')
    runsheet_type = data.get('runsheet_type')
    vehicles      = data.get('vehicles')
    skip_seed     = data.get('_skip_seed', False)

    if runsheet_date is not None and runsheet_type not in ('install', 'pickup', 'to_load'):
        return jsonify({'success': False,
                        'error': 'runsheet_type must be install, pickup, or to_load'}), 400

    if runsheet_date:
        if skip_seed:
            # Just update the job metadata — don't touch existing schedule entries
            sb_patch('jobs', f'id=eq.{job_id}', {
                'runsheet_date': runsheet_date,
                'runsheet_type': runsheet_type,
            })
        elif vehicles:
            bad = [v for v in vehicles if v not in RUNSHEET_VEHICLES]
            if bad:
                return jsonify({'success': False, 'error': f'Unknown vehicles: {bad}'}), 400
            seed_two_day_schedule(job_id, runsheet_date, runsheet_type,
                                  forced_vehicles=vehicles)
            _set_job_runsheet_date(job_id, runsheet_date, runsheet_type)
        else:
            items = sb_get('items', f'job_id=eq.{job_id}') or []
            seed_two_day_schedule(job_id, runsheet_date, runsheet_type, items=items)
            _set_job_runsheet_date(job_id, runsheet_date, runsheet_type)
    else:
        sb_delete('job_schedule', f'job_id=eq.{job_id}')
        sb_patch('jobs', f'id=eq.{job_id}', {
            'runsheet_date': None,
            'runsheet_type': None,
        })
        # Postponed with no new date yet. Only pre-install stages move to
        # on_hold — removing a date from an installed job shouldn't rewrite
        # history, so those keep their status.
        sb_patch('jobs',
                 f'id=eq.{job_id}&status=in.(ready,ready_to_load,loaded)',
                 {'status': 'on_hold'})

    return jsonify({'success': True})


@app.route('/api/runsheet/<date_str>', methods=['GET'])
def api_runsheet_day(date_str):
    """Full day data — teams, schedule entries, jobs (all referenced), tasks."""
    teams    = sb_get('day_teams',    f'date=eq.{date_str}&order=sort_order.asc,created_at.asc') or []
    schedule = sb_get('job_schedule', f'date=eq.{date_str}&order=start_time.asc,created_at.asc') or []
    tasks    = sb_get('runsheet_tasks', f'date=eq.{date_str}&order=start_time.asc') or []

    # Collect ALL job_ids referenced by schedule entries — regardless of their install date
    job_ids = list({e['job_id'] for e in schedule if e.get('job_id')})
    jobs = []
    if job_ids:
        ids_str = ','.join(job_ids)
        jobs = sb_get('jobs', f'id=in.({ids_str})') or []

    # Transfers only store the link on the receiving job, so the other half of
    # the pair usually isn't on this day. Fetch both directions, otherwise the
    # runsheet can only say "a transfer" without saying from or to where.
    jobs = _attach_transfer_partners(jobs, job_ids)

    # Where each job was loaded. The load happens the day before, so it isn't
    # in this day's schedule — without it the install tile can't say which
    # truck is already carrying the stock.
    loads = []
    if job_ids:
        try:
            loads = sb_get('job_schedule',
                           f"job_id=in.({','.join(job_ids)})&type=eq.to_load"
                           "&vehicle=not.is.null") or []
        except Exception:
            loads = []

    return jsonify({
        'teams':    teams,
        'schedule': schedule,
        'tasks':    tasks,
        'jobs':     jobs,
        'loads':    loads,
    })


def _attach_transfer_partners(jobs, job_ids):
    """Add the jobs on the other end of any transfer, so both sides can be named."""
    if not jobs:
        return jobs
    have = {j['id'] for j in jobs}
    extra_ids = {j['transfer_from_job_id'] for j in jobs
                 if j.get('is_transfer') and j.get('transfer_from_job_id')
                 and j['transfer_from_job_id'] not in have}
    extras = []
    try:
        if extra_ids:
            extras += sb_get('jobs', f"id=in.({','.join(extra_ids)})") or []
        if job_ids:
            # Jobs receiving stock FROM one of today's jobs
            extras += sb_get('jobs', f"transfer_from_job_id=in.({','.join(job_ids)})") or []
    except Exception:
        return jobs
    for e in extras:
        if e.get('id') and e['id'] not in have:
            have.add(e['id'])
            jobs.append(e)
    return jobs


@app.route('/api/runsheet/week/<start_date>', methods=['GET'])
def api_runsheet_week(start_date):
    """Read-only week overview starting at start_date (7 days).

    Fetches the whole range in four queries rather than repeating the
    per-day route seven times, which would be ~28 round trips."""
    from datetime import datetime as _dt, timedelta
    try:
        start = _dt.strptime(start_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid start date.'}), 400

    days = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
    end  = days[-1]

    teams    = sb_get('day_teams',
                      f'date=gte.{start_date}&date=lte.{end}'
                      '&order=sort_order.asc,created_at.asc') or []
    schedule = sb_get('job_schedule',
                      f'date=gte.{start_date}&date=lte.{end}'
                      '&order=start_time.asc,created_at.asc') or []
    tasks    = sb_get('runsheet_tasks',
                      f'date=gte.{start_date}&date=lte.{end}'
                      '&order=start_time.asc') or []

    job_ids = list({e['job_id'] for e in schedule if e.get('job_id')})
    jobs = []
    if job_ids:
        jobs = sb_get('jobs', f'id=in.({",".join(job_ids)})') or []

    def by_date(rows):
        out = {d: [] for d in days}
        for r in rows:
            if r.get('date') in out:
                out[r['date']].append(r)
        return out

    return jsonify({
        'success':  True,
        'days':     days,
        'teams':    by_date(teams),
        'schedule': by_date(schedule),
        'tasks':    by_date(tasks),
        'jobs':     jobs,
    })


# ── Day teams CRUD ────────────────────────────────────────────────────────────

@app.route('/api/schedule-entry', methods=['POST'])
def api_schedule_entry_create():
    """Create a job_schedule entry directly with full control."""
    data   = request.get_json()
    result = sb_post('job_schedule', {
        'job_id':     data.get('job_id'),
        'date':       data.get('date'),
        'team_id':    data.get('team_id') or None,
        'vehicle':    data.get('vehicle') or None,
        'type':       data.get('type', 'install'),
        'category':   data.get('category', 'transport'),
        'start_time': data.get('start_time') or None,
        'duration':   data.get('duration') or None,
        'notes':      data.get('notes') or None,
        # 'lead' / 'team' / 'person' were sent here but don't exist on
        # job_schedule — PostgREST rejected the whole insert, so every create
        # through this endpoint failed. Nothing ever set them anyway.
        'monday_item_id': data.get('monday_item_id') or None,
        'monday_address': data.get('monday_address') or None,
    })
    if not result:
        return jsonify({'success': False,
                        'error': sb_last_error() or 'The database rejected the new entry.'}), 400
    return jsonify({'success': True, 'entry': result[0]})

@app.route('/api/teams/<date_str>', methods=['GET'])
def api_teams_list(date_str):
    teams = sb_get('day_teams', f'date=eq.{date_str}&order=sort_order.asc,created_at.asc') or []
    return jsonify(teams)

@app.route('/api/teams/<date_str>', methods=['POST'])
def api_teams_create(date_str):
    data = request.get_json()
    result = sb_post('day_teams', {
        'date':       date_str,
        'name':       data.get('name') or None,
        'vehicle':    data.get('vehicle') or None,
        'function':   data.get('function', 'transport'),
        'lead':       data.get('lead') or None,
        'members':    data.get('members') or [],
        'colour':     data.get('colour') or None,
        'sort_order': data.get('sort_order', 0),
    })
    if not result:
        return jsonify({'success': False,
                        'error': sb_last_error() or 'The database rejected the column.'}), 400
    return jsonify({'success': True, 'team': result[0]})

@app.route('/api/teams/entry/<team_id>', methods=['PATCH'])
def api_teams_update(team_id):
    data    = request.get_json()
    payload = {}
    for f in ('name','vehicle','function','lead','members','colour','sort_order'):
        if f in data: payload[f] = data[f]
    result = sb_patch('day_teams', f'id=eq.{team_id}', payload)
    return jsonify({'success': bool(result)})

@app.route('/api/teams/entry/<team_id>', methods=['DELETE'])
def api_teams_delete(team_id):
    # Unlink schedule entries first
    sb_patch('job_schedule', f'team_id=eq.{team_id}', {'team_id': None})
    result = sb_delete('day_teams', f'id=eq.{team_id}')
    return jsonify({'success': bool(result)})

# ── Team templates ────────────────────────────────────────────────────────────

@app.route('/api/team-templates', methods=['GET'])
def api_team_templates_list():
    return jsonify(sb_get('team_templates', 'order=name.asc') or [])

@app.route('/api/team-templates', methods=['POST'])
def api_team_templates_create():
    data = request.get_json()
    result = sb_post('team_templates', {
        'name':     data.get('name'),
        'vehicle':  data.get('vehicle') or None,
        'function': data.get('function', 'transport'),
        'lead':     data.get('lead') or None,
        'members':  data.get('members') or [],
    })
    return jsonify({'success': bool(result), 'template': result[0] if result else None})

@app.route('/api/team-templates/<tmpl_id>', methods=['DELETE'])
def api_team_templates_delete(tmpl_id):
    result = sb_delete('team_templates', f'id=eq.{tmpl_id}')
    return jsonify({'success': bool(result)})


# ── Styling schedule routes ──────────────────────────────────────────────────

@app.route('/api/jobs/<job_id>/styling', methods=['GET'])
def api_job_styling_list(job_id):
    """List styling entries for a job (across all dates)."""
    entries = sb_get('job_schedule', f'job_id=eq.{job_id}&category=eq.styling&order=date.asc,start_time.asc') or []
    return jsonify(entries)

@app.route('/api/jobs/<job_id>/styling', methods=['POST'])
def api_job_styling_add(job_id):
    """Add a styling entry for a job.
    Body: {date, person, start_time, duration, notes}"""
    data = request.get_json()
    result = sb_post('job_schedule', {
        'job_id':     job_id,
        'date':       data.get('date'),
        'person':     data.get('person'),
        'lead':       data.get('lead') or None,
        'team':       data.get('team') or None,
        'vehicle':    None,
        'type':       'styling',
        'category':   'styling',
        'start_time': data.get('start_time') or None,
        'duration':   data.get('duration') or None,
        'notes':      data.get('notes') or None,
    })
    return jsonify({'success': bool(result), 'entry': result[0] if result else None})


# ── Warehouse pick routes ────────────────────────────────────────────────────

@app.route('/api/jobs/<job_id>/warehouse-pick', methods=['GET'])
def api_job_wh_pick_list(job_id):
    entries = sb_get('job_schedule', f'job_id=eq.{job_id}&category=eq.warehouse_pick&order=date.asc,start_time.asc') or []
    return jsonify(entries)

@app.route('/api/jobs/<job_id>/warehouse-pick', methods=['POST'])
def api_job_wh_pick_add(job_id):
    """Add a warehouse pick entry for a job.
    Body: {date, person, start_time, duration, notes}"""
    data = request.get_json()
    result = sb_post('job_schedule', {
        'job_id':     job_id,
        'date':       data.get('date'),
        'person':     data.get('person'),
        'lead':       data.get('lead') or None,
        'team':       data.get('team') or None,
        'vehicle':    None,
        'type':       'warehouse_pick',
        'category':   'warehouse_pick',
        'start_time': data.get('start_time') or None,
        'duration':   data.get('duration') or None,
        'notes':      data.get('notes') or None,
    })
    return jsonify({'success': bool(result), 'entry': result[0] if result else None})


@app.route('/api/tasks', methods=['POST'])
def api_task_create():
    """Create a freestanding runsheet task.
    Body: {vehicle, date, title, notes?, start_time?, duration?}
    vehicle is a vehicle name or 'ALL' for whole-team tasks."""
    data       = request.get_json()
    vehicle    = data.get('vehicle')
    date_str   = data.get('date')
    title      = (data.get('title') or '').strip()
    notes      = data.get('notes') or None
    start_time = data.get('start_time')
    duration   = data.get('duration')

    if not title:
        return jsonify({'success': False, 'error': 'title is required'}), 400
    # A column may legitimately have no vehicle (Warehouse). Tasks carry their
    # team now, so vehicle is optional — it used to be forced to 'ALL', which
    # meant "every crew" elsewhere in the app.
    if vehicle and vehicle != 'ALL' and vehicle not in RUNSHEET_VEHICLES:
        return jsonify({'success': False, 'error': f'Unknown vehicle: {vehicle}'}), 400
    if start_time is not None and start_time not in RUNSHEET_TIME_SLOTS:
        return jsonify({'success': False, 'error': 'Invalid start_time'}), 400
    if duration is not None and duration not in RUNSHEET_DURATIONS:
        return jsonify({'success': False, 'error': 'Invalid duration'}), 400

    result = sb_post('runsheet_tasks', {
        'job_id': (data.get('job_id') or None),
        # team_id was sent by the page but never saved, so every task came back
        # with no column and had to be guessed at from its vehicle. A column
        # with no vehicle (Warehouse) had nothing to guess from.
        'team_id': (data.get('team_id') or None),
        'kind': (data.get('kind') or None),      # 'break' marks time off, not work
        # The table still carries NOT NULL on vehicle from before columns
        # could be vehicle-less (Warehouse) — and breaks never send one at
        # all. An empty string satisfies the constraint and matches nothing,
        # so task resolution stays on team_id where it belongs.
        # drop_task_vehicle_notnull.sql relaxes the constraint properly.
        'vehicle': vehicle or '', 'date': date_str, 'title': title,
        'notes': notes, 'start_time': start_time, 'duration': duration,
    })
    if not result:
        return jsonify({'success': False,
                        'error': sb_last_error() or 'The database rejected the task.'}), 400
    return jsonify({'success': True, 'task': result[0]})


@app.route('/api/tasks/<task_id>', methods=['PATCH'])
def api_task_update(task_id):
    """Edit a task. Body: any of {title, notes, vehicle, start_time, duration}"""
    data    = request.get_json()
    payload = {}
    if 'job_id' in data:                       # nullable: tasks may stand alone
        payload['job_id'] = data['job_id'] or None
    if 'title' in data:
        title = (data['title'] or '').strip()
        if not title:
            return jsonify({'success': False, 'error': 'title cannot be empty'}), 400
        payload['title'] = title
    if 'notes'       in data: payload['notes']      = data['notes'] or None
    if 'start_time'  in data:
        if data['start_time'] is not None and data['start_time'] not in RUNSHEET_TIME_SLOTS:
            return jsonify({'success': False, 'error': 'Invalid start_time'}), 400
        payload['start_time'] = data['start_time']
    if 'duration'    in data:
        if data['duration'] is not None and data['duration'] not in RUNSHEET_DURATIONS:
            return jsonify({'success': False, 'error': 'Invalid duration'}), 400
        payload['duration'] = data['duration']
    if 'vehicle'     in data:
        v = data['vehicle']
        if v and v != 'ALL' and v not in RUNSHEET_VEHICLES:
            return jsonify({'success': False, 'error': f'Unknown vehicle: {v}'}), 400
        payload['vehicle'] = v or None
    if 'team_id' in data:
        payload['team_id'] = data['team_id'] or None
    result = sb_patch('runsheet_tasks', f'id=eq.{task_id}', payload)
    if result:
        return jsonify({'success': True, 'task': result[0]})
    err = sb_last_error()
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': False, 'stale': True,
                    'error': 'That task no longer exists — the page will refresh.'}), 409


@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def api_task_delete(task_id):
    result = sb_delete('runsheet_tasks', f'id=eq.{task_id}')
    return jsonify({'success': bool(result)})



@app.route('/api/jobs/<job_id>/schedule', methods=['GET'])
def api_job_schedule_list(job_id):
    rows = sb_get('job_schedule', f'job_id=eq.{job_id}&order=start_time.asc')
    return jsonify(rows or [])


@app.route('/api/jobs/<job_id>/schedule', methods=['POST'])
def api_job_schedule_add(job_id):
    """Add a vehicle assignment. Body: {vehicle?, date?, type?, start_time?, duration?, notes?}"""
    data       = request.get_json()
    vehicle    = data.get('vehicle')
    date_str   = data.get('date')
    entry_type = data.get('type')
    start_time = data.get('start_time')
    duration   = data.get('duration')
    notes      = data.get('notes') or None
    if vehicle is not None and vehicle not in RUNSHEET_VEHICLES:
        return jsonify({'success': False, 'error': f'Unknown vehicle: {vehicle}'}), 400
    if start_time is not None and start_time not in RUNSHEET_TIME_SLOTS:
        return jsonify({'success': False, 'error': 'Invalid start_time'}), 400
    if duration is not None and duration not in RUNSHEET_DURATIONS:
        return jsonify({'success': False, 'error': 'Invalid duration'}), 400
    result = sb_post('job_schedule', {
        'job_id': job_id, 'vehicle': vehicle, 'date': date_str,
        'type': entry_type, 'start_time': start_time,
        'duration': duration, 'notes': notes,
    })
    return jsonify({'success': bool(result), 'row': result[0] if result else None})


@app.route('/api/schedule/<entry_id>', methods=['PATCH'])
def api_schedule_update(entry_id):
    """Edit a schedule entry."""
    data    = request.get_json()
    payload = {}
    if 'job_id'   in data: payload['job_id']   = data['job_id'] or None
    if 'team_id'  in data: payload['team_id']  = data['team_id'] or None
    if 'vehicle'  in data:
        if data['vehicle'] is not None and data['vehicle'] not in RUNSHEET_VEHICLES:
            return jsonify({'success': False, 'error': 'Unknown vehicle'}), 400
        payload['vehicle'] = data['vehicle']
    if 'person'   in data: payload['person']   = data['person'] or None
    if 'lead'     in data: payload['lead']     = data['lead'] or None
    if 'team'     in data: payload['team']     = data['team'] or None
    if 'category' in data: payload['category'] = data['category'] or 'transport'
    if 'type'     in data: payload['type']     = data['type']
    if 'date'     in data: payload['date']     = data['date']
    if 'start_time' in data:
        if data['start_time'] is not None and data['start_time'] not in RUNSHEET_TIME_SLOTS:
            return jsonify({'success': False, 'error': 'Invalid start_time'}), 400
        payload['start_time'] = data['start_time']
    if 'duration' in data:
        dur = data['duration']
        if dur is not None:
            try: dur = int(dur)
            except: return jsonify({'success': False, 'error': 'Invalid duration'}), 400
        payload['duration'] = dur
    if 'notes'    in data: payload['notes'] = data['notes'] or None
    result = sb_patch('job_schedule', f'id=eq.{entry_id}', payload)
    if result:
        return jsonify({'success': True, 'entry': result[0]})

    # An empty result and a rejected write are different problems: one means
    # the row is gone (the page is stale and should reload), the other means
    # the database refused the change. Saying which saves a lot of guesswork.
    err = sb_last_error()
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': False, 'stale': True,
                    'error': 'That tile no longer exists — the page will refresh.'}), 409


@app.route('/api/schedule/<entry_id>', methods=['DELETE'])
def api_schedule_delete(entry_id):
    result = sb_delete('job_schedule', f'id=eq.{entry_id}')
    if not result and sb_last_error():
        return jsonify({'success': False, 'error': sb_last_error()}), 400
    return jsonify({'success': bool(result)})


# ── Vehicle day crew ──

@app.route('/api/crew/<date_str>/<vehicle>', methods=['PUT'])
def api_crew_upsert(date_str, vehicle):
    """Set (or replace) the crew for a vehicle on a specific day.
    Uses upsert on the unique (vehicle, date) constraint.
    Body: {lead?, offsiders?[]}"""
    if vehicle not in RUNSHEET_VEHICLES:
        return jsonify({'success': False, 'error': 'Unknown vehicle'}), 400
    data      = request.get_json()
    lead      = data.get('lead')
    offsiders = data.get('offsiders', [])
    if lead is not None and lead not in RUNSHEET_WORKERS:
        return jsonify({'success': False, 'error': 'Unknown lead'}), 400
    bad = [w for w in offsiders if w not in RUNSHEET_WORKERS]
    if bad:
        return jsonify({'success': False, 'error': f'Unknown workers: {bad}'}), 400

    # Try update first; if nothing matched, insert
    existing = sb_get('vehicle_day_crew', f'vehicle=eq.{vehicle}&date=eq.{date_str}')
    if existing:
        result = sb_patch('vehicle_day_crew',
                          f'vehicle=eq.{vehicle}&date=eq.{date_str}',
                          {'lead': lead, 'offsiders': offsiders})
    else:
        result = sb_post('vehicle_day_crew',
                         {'vehicle': vehicle, 'date': date_str,
                          'lead': lead, 'offsiders': offsiders})
    return jsonify({'success': bool(result)})


@app.route('/api/runsheet-config', methods=['GET'])
def api_runsheet_config():
    return jsonify({
        'vehicles':   RUNSHEET_VEHICLES,
        'stylists':   RUNSHEET_STYLISTS,
        'drivers':    RUNSHEET_DRIVERS,
        'workers':    RUNSHEET_WORKERS,
        'time_slots': RUNSHEET_TIME_SLOTS,
        'durations':  RUNSHEET_DURATIONS,
    })


@app.route('/api/jobs/<job_id>/transfer', methods=['PATCH'])
def api_job_transfer(job_id):
    """Set or clear this job's transfer-from relationship after it's
    already been created. Previously this could only be set once, at
    label-generation time, in save_job_to_db() — there was no way to
    mark an existing job as a transfer after the fact.

    Body: {transfer_from_job_id} — a job id to mark this job as
    transferring from that job, or null to clear the transfer entirely
    (sets is_transfer back to false).

    There's no separate "transfer to" version of this route. Setting
    "Transfer To <job B>" from job A's tile is really "set job B's
    transfer-from to job A" — the frontend achieves that by calling this
    same route, but with job B's id as the URL parameter and job A's id
    in the body, not by adding a second endpoint. This keeps the
    transfer relationship correct by construction: it's always stored as
    is_transfer + transfer_from_job_id on the receiving job, the same
    place it's always lived, "transferring to" is still only ever
    derived (see /api/jobs/<id>'s transferring_to field) rather than
    given a second, independently-editable home that could fall out of
    sync with this one.

    No self-reference allowed — a job can't transfer from itself."""
    data = request.get_json()
    transfer_from_job_id = data.get('transfer_from_job_id')
    if transfer_from_job_id == job_id:
        return jsonify({'success': False, 'error': 'A job cannot transfer from itself'}), 400
    result = sb_patch('jobs', f'id=eq.{job_id}', {
        'is_transfer': bool(transfer_from_job_id),
        'transfer_from_job_id': transfer_from_job_id,
    })
    return jsonify({'success': bool(result)})

@app.route('/api/schedule-entry/<entry_id>/actual', methods=['POST'])
def api_entry_actual(entry_id):
    """One-tap start/finish stamps from the crew on site.

    which='start' is stamped by Navigate; which='done' by the Done button.
    Only the first tap of each wins — repeat taps and page refreshes must
    not move a time that's already been recorded."""
    data  = request.get_json(silent=True) or {}
    which = data.get('which')
    if which not in ('start', 'done'):
        return jsonify({'success': False, 'error': "which must be 'start' or 'done'"}), 400

    got = sb_get('job_schedule', f'id=eq.{entry_id}')
    if not got:
        return jsonify({'success': False, 'error': 'That entry no longer exists.'}), 404
    entry = got[0]

    field = 'actual_start' if which == 'start' else 'actual_end'
    if entry.get(field):
        # Already stamped — idempotent, and the first tap is the honest one
        return jsonify({'success': True, 'entry': entry, 'already': True})

    now = datetime.utcnow().isoformat() + 'Z'
    patch = {field: now}
    # A crew that taps Done without ever tapping Navigate still gives us an
    # end time; leave start empty rather than inventing one.
    result = sb_patch('job_schedule', f'id=eq.{entry_id}', patch)
    if result:
        return jsonify({'success': True, 'entry': result[0]})
    err = sb_last_error()
    return jsonify({'success': False, 'error': err or 'Save failed'}), 400


@app.route('/api/jobs/<job_id>/eta', methods=['POST'])
def api_job_eta(job_id):
    """Calculate driving ETA from someone's current position (sent by
    the browser via the Geolocation API, triggered when they tap the
    address on /driver/<job_id> or /stylist/<job_id>) to this job's
    address, save it on the job so it shows on the /jobs tile, and post
    it to Slack — all as one continuous action with no separate
    confirmation step. (An earlier version asked "post this to Slack?"
    before posting; that extra tap meant nothing reached Slack unless
    someone noticed and answered the prompt, which defeated the point —
    the actual desired flow is: tap address, grant location, Maps opens,
    Slack gets the message, no manual step in between.)

    Body: {lat, lng, role} where role is "truck" or "stylist" — decides
    which pair of columns gets written (truck_eta_text/calculated_at or
    stylist_eta_text/calculated_at) and which label shows in the Slack
    message. Kept as one endpoint with a role flag rather than two
    separate routes, since the calculation itself is identical either
    way — only the destination column and message wording differ.

    See get_truck_eta() for the actual Distance Matrix call and why it
    fails silently rather than erroring — a missing API key or a network
    hiccup shouldn't block the driver/stylist from just opening Maps,
    which is the primary action either click triggers. Slack posting
    follows the same philosophy: see notify_slack_eta() for why a failed
    or unconfigured webhook never blocks anything either."""
    data = request.get_json()
    lat  = data.get('lat')
    lng  = data.get('lng')
    role = data.get('role', 'truck')
    if lat is None or lng is None:
        return jsonify({'success': False, 'error': 'lat/lng required'}), 400
    # 'team' was sent by the team view for a while and rejected here, so no
    # ETA reached Slack from that page. Treat it as a transport run rather than
    # failing, so an older cached page still works.
    if role == 'team':
        role = 'truck'
    if role not in ('truck', 'stylist'):
        return jsonify({'success': False, 'error': 'role must be "truck" or "stylist"'}), 400

    job_rows = sb_get('jobs', f'id=eq.{job_id}')
    if not job_rows:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    job     = job_rows[0]
    address = job.get('address', '')

    eta_text = get_truck_eta(lat, lng, address)
    if eta_text is None:
        # Couldn't calculate — leave any previous ETA untouched rather than
        # overwriting a good value with nothing just because this attempt failed.
        return jsonify({'success': False, 'eta_text': None})

    text_col = f'{role}_eta_text'
    time_col = f'{role}_eta_calculated_at'
    sb_patch('jobs', f'id=eq.{job_id}', {
        text_col: eta_text,
        time_col: datetime.utcnow().isoformat(),
    })

    slack_posted = notify_slack_eta(job, role, eta_text)

    return jsonify({'success': True, 'eta_text': eta_text, 'role': role, 'slack_posted': slack_posted})

@app.route('/api/items/<item_id>/check', methods=['PATCH'])
def api_item_check(item_id):
    data    = request.get_json()
    payload = {}
    if 'checked'          in data: payload['checked']          = data['checked']
    if 'on_truck'         in data: payload['on_truck']         = data['on_truck']
    if 'bay_location'     in data: payload['bay_location']     = data['bay_location'] or None
    if 'room'             in data: payload['room']             = data['room'] or None
    if 'notes'            in data: payload['notes']            = data['notes']
    if 'picked'           in data: payload['picked']           = data['picked']
    if 'photo_url'        in data: payload['photo_url']        = data['photo_url']
    if 'is_transfer_item' in data: payload['is_transfer_item'] = data['is_transfer_item']
    if 'not_transferring' in data: payload['not_transferring'] = data['not_transferring']
    result = sb_patch('items', f'id=eq.{item_id}', payload)
    return jsonify({'success': bool(result)})

@app.route('/api/jobs/<job_id>/items', methods=['POST'])
def api_add_item(job_id):
    data   = request.get_json()
    result = sb_post('items', {
        'job_id':      job_id,
        'serial':      data['serial'],
        'room':        data['room'],
        'description': data.get('description', ''),
        'is_extra':    data.get('is_extra', False),
        'checked':     False,
    })
    return jsonify(result[0] if result else {'error': 'Failed'})

@app.route('/api/items/<item_id>', methods=['DELETE'])
def api_delete_item(item_id):
    result = sb_delete('items', f'id=eq.{item_id}')
    return jsonify({'success': result})

@app.route('/api/items/<item_id>/photos', methods=['GET'])
def api_item_photos_list(item_id):
    """All photos for an item, oldest first."""
    rows = sb_get('item_photos', f'item_id=eq.{item_id}&order=created_at.asc')
    return jsonify(rows or [])


# ── Furniture catalogue helpers ──
# Category mapping mirrors the driver interface's ITEM_CATEGORIES.
# Descriptions that don't match any category are not catalogued (accessories,
# extras, ensembles components, etc. are excluded).
_CATALOGUE_CATEGORIES = [
    ('Storage & Consoles',  ['console','buffet','dresser','entertainment unit','etu','shelv','bookshelf','wardrobe']),
    ('Bedside Tables',      ['bedside']),
    ('Chairs',              ['chair','barstool','stool','bench seat']),
    ('Sofas',               ['sofa','settee','lounge']),
    ('Rugs',                ['rug']),
    ('Linen and Cushions',  ['linen','coverlet','towel','throw','cushion','pillow']),
    ('Beds & Mattresses',   ['ensemble','mattress','bed frame','headboard','bedhead']),
    ('Tables',              ['table','desk']),
    ('Artwork',             ['artwork']),
    ('Floor Lamps',         ['floor lamp']),
    ('Outdoor',             ['outdoor']),
    ('Accessories',         ['accessor','centrepiece','mirror']),
]

def catalogue_type_for(description):
    """Return the catalogue type label for a description, or None if it
    shouldn't be catalogued (e.g. blank extras, unrecognised items)."""
    if not description:
        return None
    d = description.lower()
    for label, keywords in _CATALOGUE_CATEGORIES:
        if any(k in d for k in keywords):
            return label
    return None


@app.route('/api/items/<item_id>/photos', methods=['POST'])
def api_item_photos_add(item_id):
    """Record a new photo URL for an item. The actual file upload goes
    direct from the browser to Supabase Storage — this just saves the URL.
    Also updates items.photo_url to the new URL so the driver interface
    always shows the most recently added photo.

    Auto-catalogues the FIRST photo of an item into furniture_catalogue
    if the item description maps to a known furniture category. Subsequent
    photos for the same item are not re-catalogued (one entry per item per job)."""
    data = request.get_json()
    url  = data.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'url required'}), 400

    # Check if this is the first photo for this item
    existing_photos = sb_get('item_photos', f'item_id=eq.{item_id}')
    is_first_photo  = not existing_photos

    result = sb_post('item_photos', {'item_id': item_id, 'url': url})
    sb_patch('items', f'id=eq.{item_id}', {'photo_url': url})

    # Auto-catalogue: only on first photo, only for catalogueable item types
    if is_first_photo:
        item = sb_get('items', f'id=eq.{item_id}')
        if item:
            item      = item[0]
            desc      = item.get('description', '')
            room      = item.get('room', '')
            job_id    = item.get('job_id')
            cat_type  = catalogue_type_for(desc)
            if cat_type:
                sb_post('furniture_catalogue', {
                    'type':         cat_type,
                    'room_context': room or None,
                    'description':  desc,
                    'photo_url':    url,
                    'item_id':      item_id,
                    'job_id':       job_id,
                })

    return jsonify({'success': bool(result), 'photo': result[0] if result else None})


@app.route('/api/catalogue', methods=['GET'])
def api_catalogue_list():
    """All catalogue entries, newest first. Optionally filter by type and/or
    room_context via query params: /api/catalogue?type=Sofas&room=Living+Room"""
    filters = 'order=created_at.desc'
    t = request.args.get('type')
    r = request.args.get('room')
    if t: filters += f'&type=eq.{t}'
    if r: filters += f'&room_context=eq.{r}'
    rows = sb_get('furniture_catalogue', filters)
    return jsonify(rows or [])


@app.route('/api/catalogue', methods=['POST'])
def api_catalogue_add():
    """Manually add a catalogue entry. Body: {type, room_context?, description?, photo_url}"""
    data = request.get_json()
    url  = (data.get('photo_url') or '').strip()
    cat_type = (data.get('type') or '').strip()
    if not url or not cat_type:
        return jsonify({'success': False, 'error': 'type and photo_url required'}), 400
    result = sb_post('furniture_catalogue', {
        'type':               cat_type,
        'room_context':       data.get('room_context') or None,
        'description':        data.get('description') or None,
        'warehouse_location': data.get('warehouse_location') or None,
        'photo_url':          url,
        'item_id':            None,
        'job_id':             None,
    })
    return jsonify({'success': bool(result), 'entry': result[0] if result else None})


@app.route('/api/catalogue/<entry_id>', methods=['PATCH'])
def api_catalogue_update(entry_id):
    """Edit a catalogue entry. Body: any of {type, room_context, description, photo_url}"""
    data    = request.get_json()
    payload = {}
    if 'type'               in data: payload['type']               = data['type']
    if 'room_context'       in data: payload['room_context']       = data['room_context'] or None
    if 'description'        in data: payload['description']        = data['description'] or None
    if 'warehouse_location' in data: payload['warehouse_location'] = data['warehouse_location'] or None
    if 'photo_url'          in data: payload['photo_url']          = data['photo_url']
    result = sb_patch('furniture_catalogue', f'id=eq.{entry_id}', payload)
    return jsonify({'success': bool(result)})


@app.route('/api/catalogue/<entry_id>', methods=['DELETE'])
def api_catalogue_delete(entry_id):
    result = sb_delete('furniture_catalogue', f'id=eq.{entry_id}')
    return jsonify({'success': bool(result)})


@app.route('/api/catalogue/types', methods=['GET'])
def api_catalogue_types():
    """All distinct types + room_contexts in the catalogue, for building
    the filter UI without a full table scan."""
    rows = sb_get('furniture_catalogue', 'select=type,room_context')
    types = sorted({r['type'] for r in (rows or []) if r.get('type')})
    rooms = sorted({r['room_context'] for r in (rows or []) if r.get('room_context')})
    # Also include the full defined list so empty categories still appear
    all_types = [t for t,_ in _CATALOGUE_CATEGORIES]
    return jsonify({'types': types, 'rooms': rooms, 'all_types': all_types})

@app.route('/api/item-photos/<photo_id>', methods=['DELETE'])
def api_item_photo_delete(photo_id):
    """Delete a single photo record. If it was the primary photo (photo_url
    on the item), update photo_url to the next most recent photo instead,
    or null if no photos remain."""
    # Find the photo to know which item it belongs to
    photo = sb_get('item_photos', f'id=eq.{photo_id}')
    if photo:
        item_id = photo[0]['item_id']
        sb_delete('item_photos', f'id=eq.{photo_id}')
        # Re-derive photo_url from remaining photos (most recent)
        remaining = sb_get('item_photos', f'item_id=eq.{item_id}&order=created_at.desc')
        new_primary = remaining[0]['url'] if remaining else None
        sb_patch('items', f'id=eq.{item_id}', {'photo_url': new_primary})
    else:
        sb_delete('item_photos', f'id=eq.{photo_id}')
    return jsonify({'success': True})


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
def api_delete_job(job_id):
    """Delete a job and all its associated data. Cascade order matters:
    items and room_notes must go before the job row itself (Supabase
    won't cascade these automatically since they have no FK deletion
    rule — only transfer_from_job_id does, which is handled by the DB's
    own ON DELETE SET NULL constraint and needs no code here).
    This is a permanent, irreversible action — the confirmation prompt
    is on the frontend, not the backend. If building per-user permissions
    later, this route is the natural place to add a "admin only" check.
    """
    sb_delete('items',      f'job_id=eq.{job_id}')
    sb_delete('room_notes', f'job_id=eq.{job_id}')
    result = sb_delete('jobs', f'id=eq.{job_id}')
    return jsonify({'success': bool(result)})


# ── Monday.com sync ──

@app.route('/monday')
def monday_page():
    return open(os.path.join(app.template_folder, 'monday.html')).read()

def norm_address(s):
    """Normalise an address for matching: expand abbreviations, strip punctuation."""
    if not s: return ''
    s = s.lower().strip()
    abbrevs = [
        (r'\bst\b', 'street'), (r'\brd\b', 'road'), (r'\bave?\b', 'avenue'),
        (r'\bdr\b', 'drive'), (r'\bcr?\b', 'crescent'), (r'\bct\b', 'court'),
        (r'\bpl\b', 'place'), (r'\bblvd\b', 'boulevard'), (r'\bln\b', 'lane'),
        (r'\bpde\b', 'parade'), (r'\bhwy\b', 'highway'), (r'\bmt\b', 'mount'),
    ]
    for pattern, replacement in abbrevs:
        s = re.sub(pattern, replacement, s)
    s = re.sub(r'[,\.\-/#]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def address_street_key(s):
    """Street number + first word only — for matching when suburb/state/postcode differ."""
    parts = norm_address(s).split()
    if len(parts) >= 2:
        return ' '.join(parts[:2])
    return norm_address(s)

def link_placeholder_schedule_entries(job_id, job_address):
    """Called after a job is created/updated — if any Monday-sourced schedule
    placeholder (job_id null, monday_address set) matches this job's address,
    attach it to the new job. This is what makes 'once it is a job, please
    update it' automatic: no manual re-pull needed once the job exists."""
    if not job_address:
        return
    placeholders = sb_get('job_schedule', 'job_id=is.null&monday_address=not.is.null') or []
    target_norm = norm_address(job_address)
    target_key  = address_street_key(job_address)
    for p in placeholders:
        p_addr = p.get('monday_address', '')
        if norm_address(p_addr) == target_norm or address_street_key(p_addr) == target_key:
            sb_patch('job_schedule', f'id=eq.{p["id"]}', {'job_id': job_id})


def get_monday_board_data():
    """Fetch board groups + items from Monday, match jobs by address.
    READ-ONLY — every call here is a GraphQL `query`, never a `mutation`.
    Nothing in this function can write back to Monday under any circumstance.
    Returns a dict on success, or {'error': ...} on failure — this outer
    wrapper guarantees a dict is always returned, never a raw exception,
    so callers (and Flask) can never end up serving an HTML error page
    where JSON was expected."""
    try:
        return _get_monday_board_data_inner()
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}


def _get_monday_board_data_inner():
    # Step 1: get board structure — groups and ALL column definitions
    structure_q = '''
    query($bid: ID!) {
      boards(ids: [$bid]) {
        name
        groups { id title }
        columns { id title type }
      }
    }'''
    structure = monday_query(structure_q, {'bid': MONDAY_BOARD_ID})
    if 'errors' in structure:
        return {'error': structure['errors']}

    board      = structure['data']['boards'][0]
    groups_map = {g['id']: g['title'] for g in board['groups']}
    columns    = board['columns']
    col_title_by_id = {c['id']: c['title'] for c in columns}

    # Identify relevant columns by keyword in their title (case-insensitive).
    def find_col(*keywords, exclude=None):
        exclude = exclude or []
        for c in columns:
            t = c['title'].lower()
            if any(k in t for k in keywords) and not any(x in t for x in exclude):
                return c['id']
        return None

    address_col_id  = find_col('property', 'address', 'project')
    type_col_id     = find_col('type')
    size_col_id     = find_col('size', 'sqm', 'sq m', 'm2')
    style_col_id    = find_col('style', 'styling', 'package', 'look', exclude=['stylist'])
    install_date_id = find_col('install date', 'install')
    end_date_id     = find_col('end date', 'de-install', 'deinstall', 'pickup date', 'finish')
    date_cols = [c['id'] for c in columns if c['type'] == 'date']
    if install_date_id and col_title_by_id.get(install_date_id) and \
       next((c for c in columns if c['id']==install_date_id), {}).get('type') != 'date':
        install_date_id = None
    if not install_date_id and date_cols:
        install_date_id = date_cols[0]
    if not end_date_id and len(date_cols) > 1:
        end_date_id = next((d for d in date_cols if d != install_date_id), None)
    status_col_id = next(
        (c['id'] for c in columns if c['type'] == 'color' or 'status' in c['title'].lower()), None
    )

    # Step 2: fetch all items with column values, following pagination fully
    items_q = '''
    query($bid: ID!) {
      boards(ids: [$bid]) {
        items_page(limit: 100) {
          cursor
          items {
            id name
            group { id }
            column_values { id text value }
          }
        }
      }
    }'''
    next_q = '''
    query($cursor: String!) {
      next_items_page(limit: 100, cursor: $cursor) {
        cursor
        items {
          id name
          group { id }
          column_values { id text value }
        }
      }
    }'''
    all_items = []
    try:
        result = monday_query(items_q, {'bid': MONDAY_BOARD_ID})
        if 'errors' in result:
            return {'error': result['errors']}
        page = result['data']['boards'][0]['items_page']
        all_items += page['items']
        cursor = page.get('cursor')

        pages_fetched = 1
        while cursor and pages_fetched < 10:  # safety cap: 1000 items max
            next_result = monday_query(next_q, {'cursor': cursor})
            if 'errors' in next_result:
                break
            next_page = next_result['data']['next_items_page']
            all_items += next_page['items']
            cursor = next_page.get('cursor')
            pages_fetched += 1
    except Exception as e:
        return {'error': str(e)}

    # Step 3: load all Luma jobs for address matching
    luma_jobs = sb_get('jobs', 'order=created_at.desc') or []
    luma_by_addr = {}
    luma_by_key  = {}
    for j in luma_jobs:
        addr = j.get('address','')
        if addr:
            luma_by_addr[norm_address(addr)] = j
            luma_by_key[address_street_key(addr)] = j

    # Step 4: build response — group items, attach luma job if matched
    groups_out = {}
    for item in all_items:
        gid     = item['group']['id']
        gtitle  = groups_map.get(gid, gid)
        col_map = {cv['id']: cv for cv in item['column_values']}

        def col_text(col_id):
            return col_map.get(col_id, {}).get('text', '') if col_id else ''

        address_val = col_text(address_col_id) or item['name']
        type_val    = col_text(type_col_id)
        size_val    = col_text(size_col_id)
        style_val   = col_text(style_col_id) if style_col_id else ''
        install_dt  = col_text(install_date_id)
        end_dt      = col_text(end_date_id)
        status_val  = col_text(status_col_id)

        luma_job = (luma_by_addr.get(norm_address(address_val)) or
                    luma_by_key.get(address_street_key(address_val)))

        raw_columns = {col_title_by_id.get(cid, cid): cv.get('text','')
                       for cid, cv in col_map.items() if cv.get('text')}

        row = {
            'monday_id':         item['id'],
            'name':              item['name'],
            'address':           address_val,
            'install_type':      type_val,
            'install_size':      size_val,
            'install_style':     style_val,
            'install_date':      install_dt,
            'end_date':          end_dt,
            'status':            status_val,
            'group_id':          gid,
            'group_title':       gtitle,
            'raw_columns':       raw_columns,
            'luma_job':    {
                'id':           luma_job['id'],
                'job_ref':      luma_job.get('job_ref',''),
                'job_number':   luma_job.get('job_number',''),
                'runsheet_date':luma_job.get('runsheet_date',''),
                'address':      luma_job.get('address',''),
                'status':       luma_job.get('status',''),
                'property_type':  luma_job.get('property_type'),
                'property_size':  luma_job.get('property_size'),
                'property_style': luma_job.get('property_style'),
            } if luma_job else None,
        }

        if gtitle not in groups_out:
            groups_out[gtitle] = []
        groups_out[gtitle].append(row)

    return {
        'board_name': board['name'],
        'groups':     groups_out,
        'columns':    [{'id': c['id'], 'title': c['title'], 'type': c['type']} for c in columns],
        'detected_columns': {
            'address':  col_title_by_id.get(address_col_id),
            'type':     col_title_by_id.get(type_col_id),
            'size':     col_title_by_id.get(size_col_id),
            'style':    col_title_by_id.get(style_col_id),
            'install_date': col_title_by_id.get(install_date_id),
            'end_date': col_title_by_id.get(end_date_id),
            'status':   col_title_by_id.get(status_col_id),
        },
    }


@app.route('/api/monday/board')
def api_monday_board():
    try:
        data = get_monday_board_data()
        if 'error' in data:
            return jsonify(data), 500
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'{type(e).__name__}: {e}'}), 500


# Groups that "Pull into Luma" acts on — matched case-insensitively, trimmed.
MONDAY_PULL_GROUPS = {'quote accepted', 'ready to pick'}

# Groups whose dates we never sync back (historic / finished work)
MONDAY_IGNORE_GROUPS = {'completed'}

# Monday label meaning "the install is done, bring it back"
MONDAY_COLLECT_LABEL = 'ready to collect'

# Each job update is its own request to Supabase, so an unbounded pull can
# outrun the platform's request timeout — which shows up as a 500 with an
# empty body, because the worker is killed before Flask can reply. Cap the
# writes per run and report what's left; a second click finishes the job.
# Once the backlog clears, normal pulls do almost no writes at all.
MONDAY_MAX_JOB_WRITES = 25


def monday_prev_business_day(date_str):
    """Previous working day — LOAD happens the business day before INSTALL."""
    from datetime import datetime as _dt, timedelta
    try:
        d = _dt.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return None
    w = d.weekday()
    d = d - timedelta(days=3 if w == 0 else 2 if w == 6 else 1)
    return d.strftime('%Y-%m-%d')


def monday_label_norm(s):
    """Lowercase, strip punctuation, collapse spaces — so 'Ready To Collect',
    'ready-to-collect' and 'Ready  to collect' all compare equal."""
    if not s:
        return ''
    s = re.sub(r'[^a-z0-9]+', ' ', str(s).lower())
    return re.sub(r'\s+', ' ', s).strip()

@app.route('/api/monday/pull', methods=['POST'])
def api_monday_pull():
    """Pull matched install dates into existing Luma jobs, and add every item
    in the 'Quote Accepted' / 'Ready to Pick' groups to the runsheet's
    unscheduled tray — matched items get their real job_id, unmatched items
    get an address-only placeholder that link_placeholder_schedule_entries()
    will attach automatically once a matching job is created.

    WRITES to Supabase (jobs, job_schedule) only. Still never writes to
    Monday — every Monday call underneath is a read-only query."""
    try:
        return _api_monday_pull_inner()
    except Exception as e:
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


def _api_monday_pull_inner():
    data = get_monday_board_data()
    if 'error' in data:
        return jsonify({'success': False, 'error': data['error']}), 500

    date_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    dates_updated       = 0
    statuses_updated    = 0
    scheduled_matched   = 0
    scheduled_unmatched = 0
    skipped_no_date     = 0
    skipped_errors      = 0
    writes_remaining    = 0
    details_updated     = 0
    completed_updated   = 0
    tiles_created       = 0
    tiles_updated       = 0
    changes             = []   # human-readable log returned to the page

    # Pass 1 — collect everything we might act on, in one sweep.
    #   date sync   : every group except the ignored ones
    #   tray tiles  : only the pull groups (quote accepted / ready to pick)
    #   status move : items whose group OR status column says ready to collect
    actionable = []
    details_only = []          # ignored groups: property details + completion
    for gtitle, items in data['groups'].items():
        gnorm = monday_label_norm(gtitle)
        if gnorm in MONDAY_IGNORE_GROUPS:
            # Completed work still describes a real property, and a pickup is
            # scheduled precisely when a job has finished — so its Monday item
            # has usually moved to Completed. Skipping these entirely left
            # pickup tiles with no type or size. Dates, tiles and status are
            # still left alone for these; only the description is synced.
            for item in items:
                if item.get('luma_job'):
                    details_only.append(item)
            continue
        for item in items:
            actionable.append((gnorm, item))

    if not actionable and not details_only:
        return jsonify({'success': True, 'dates_updated': 0, 'statuses_updated': 0,
                        'scheduled_matched': 0, 'scheduled_unmatched': 0,
                        'skipped_no_date': 0, 'skipped_errors': 0, 'changes': []})

    # One query for the schedule rows we may need to update, keyed by
    # (monday_item_id, type) so LOAD and INSTALL rows don't collide.
    # Only pull-group items ever get a tray tile, so don't build a giant
    # in.() filter out of the whole board.
    monday_ids = [i['monday_id'] for g, i in actionable if g in MONDAY_PULL_GROUPS]
    ids_str = ','.join(monday_ids)
    existing_rows = sb_get('job_schedule', f'monday_item_id=in.({ids_str})') if ids_str else []
    existing_by_key = {(r['monday_item_id'], r.get('type')): r
                       for r in (existing_rows or []) if r.get('monday_item_id')}

    # Also look up UNSCHEDULED tiles by job, so a tile created by the label
    # upload (which carries no monday_item_id) is recognised instead of
    # being duplicated on every pull. Deliberately limited to tiles with no
    # team and no time: a second tile that someone has actually placed on a
    # vehicle is intentional and must never be touched.
    job_ids = [i['luma_job']['id'] for g, i in actionable
               if g in MONDAY_PULL_GROUPS and i.get('luma_job')]
    unscheduled_by_job = {}
    if job_ids:
        rows = sb_get('job_schedule',
                      f'job_id=in.({",".join(set(job_ids))})'
                      '&team_id=is.null&start_time=is.null') or []
        for r in rows:
            unscheduled_by_job.setdefault((r['job_id'], r.get('type'), r.get('date')), r)

    to_insert = []

    held_ids = []
    for gnorm, item in actionable:
        try:
            install_date = item.get('install_date', '')
            has_date     = bool(install_date and date_re.match(install_date))
            luma_job     = item.get('luma_job')
            in_pull_group = gnorm in MONDAY_PULL_GROUPS

            # Is Monday saying this one is ready to come back? The label may
            # live in the group title or in the item's status column.
            says_collect = (gnorm == monday_label_norm(MONDAY_COLLECT_LABEL)
                            or monday_label_norm(item.get('status')) ==
                               monday_label_norm(MONDAY_COLLECT_LABEL))

            if luma_job:
                job_id  = luma_job['id']
                job_ref = luma_job.get('job_ref') or luma_job.get('job_number') or job_id[:8]
                patch   = {}

                # (a) Keep the install date current — only write when it differs
                if has_date and luma_job.get('runsheet_date') != install_date:
                    patch['runsheet_date'] = install_date
                    patch['runsheet_type'] = 'install'
                    # A date arriving for a held job releases the hold
                    if luma_job.get('status') == 'on_hold':
                        patch['status'] = 'ready'

                # (a2) Monday's date was CLEARED — postponed with no new date.
                # Deliberately narrow: only when the cell is genuinely blank,
                # never when a value is present but unparseable. A parse
                # failure or an API hiccup must not unschedule a real job.
                date_blank = not (install_date or '').strip()
                if (date_blank and luma_job.get('runsheet_date')
                        and luma_job.get('status') in ('ready', 'ready_to_load', 'loaded')):
                    patch['runsheet_date'] = None
                    patch['runsheet_type'] = None
                    patch['status'] = 'on_hold'
                    held_ids.append(job_id)

                # (b) Installed jobs that Monday now lists as ready to collect.
                #     Deliberately one-directional and narrow: this is the only
                #     status transition the sync will ever make.
                if says_collect and luma_job.get('status') == 'installed':
                    patch['status'] = 'ready_to_collect'

                # (c) Property details. The jobs page and the runsheet tiles
                #     already display these; nothing had ever written them.
                #     Only set a field when Monday actually has a value, so a
                #     blank column never wipes something entered by hand.
                for field, value in (('property_type',  item.get('install_type')),
                                     ('property_size',  item.get('install_size')),
                                     ('property_style', item.get('install_style'))):
                    val = (value or '').strip()
                    if val and luma_job.get(field) != val:
                        patch[field] = val

                if patch and (dates_updated + statuses_updated) >= MONDAY_MAX_JOB_WRITES:
                    writes_remaining += 1
                    patch = {}          # defer to the next run
                if patch:
                    sb_patch('jobs', f'id=eq.{job_id}', patch)
                    if job_id in held_ids:
                        # Clear the scheduled tiles as well — the job is no
                        # longer happening on that day.
                        sb_delete('job_schedule', f'job_id=eq.{job_id}')
                        dates_updated += 1
                        changes.append(f'#{job_ref} date removed in Monday — on hold')
                    elif 'runsheet_date' in patch:
                        dates_updated += 1
                        changes.append(f'#{job_ref} install date set to {install_date}')
                    if 'status' in patch:
                        statuses_updated += 1
                    prop_fields = [f for f in ('property_type', 'property_size', 'property_style')
                                   if f in patch]
                    if prop_fields:
                        details_updated += 1
                        changes.append(f'#{job_ref} property details updated')
                        changes.append(f'#{job_ref} marked Ready to Collect')

            # (c) Tray tiles, still only for the two pull groups
            if not in_pull_group:
                continue
            if not has_date:
                skipped_no_date += 1
                continue

            # INSTALL on the date, plus LOAD the business day before —
            # matching what the calendar/label path already seeds, so a
            # Monday-sourced job isn't missing from the load day.
            load_date = monday_prev_business_day(install_date)
            wanted = [('install', install_date)]
            if load_date:
                wanted.append(('to_load', load_date))

            for etype, edate in wanted:
                entry = {
                    'job_id':          luma_job['id'] if luma_job else None,
                    'monday_item_id':  item['monday_id'],
                    'monday_address':  item['address'],
                    'date':            edate,
                    'type':            etype,
                    'vehicle':         None,
                    'team_id':         None,
                    'start_time':      None,
                    'duration':        None,
                    'notes':           None,
                }

                # Find an existing tile: first by Monday id, then — for tiles
                # created by the label upload — by job/type/date.
                existing = existing_by_key.get((item['monday_id'], etype))
                if not existing and luma_job:
                    existing = unscheduled_by_job.get((luma_job['id'], etype, edate))

                if existing:
                    # Only write if something actually differs, so repeat
                    # pulls don't hammer the database for no reason.
                    if any(existing.get(k) != v for k, v in entry.items()
                           if k in ('job_id', 'monday_item_id', 'date', 'type')):
                        sb_patch('job_schedule', f'id=eq.{existing["id"]}', entry)
                        tiles_updated += 1
                else:
                    to_insert.append(entry)
                    tiles_created += 1

            if luma_job: scheduled_matched += 1
            else:        scheduled_unmatched += 1

        except Exception:
            skipped_errors += 1
            continue

    # Property details for jobs whose Monday item sits in an ignored group.
    # Same cap as everything else, so a big first run drains over a few clicks.
    for item in details_only:
        try:
            luma_job = item['luma_job']
            patch = {}
            for field, value in (('property_type',  item.get('install_type')),
                                 ('property_size',  item.get('install_size')),
                                 ('property_style', item.get('install_style'))):
                val = (value or '').strip()
                if val and luma_job.get(field) != val:
                    patch[field] = val

            # Monday says the work is finished. Jobs that are installed or
            # already awaiting collection move on; a job still to pick or
            # load is NOT completed just because its Monday item was filed
            # away. 'archived' is what the app stores for Completed (see
            # api_job_status), and archived jobs drop out of the default
            # jobs list.
            completing = luma_job.get('status') in ('installed', 'ready_to_collect')
            if completing:
                patch['status'] = 'archived'

            if not patch:
                continue
            if (dates_updated + statuses_updated + details_updated) >= MONDAY_MAX_JOB_WRITES:
                writes_remaining += 1
                continue
            sb_patch('jobs', f"id=eq.{luma_job['id']}", patch)
            ref = luma_job.get('job_ref') or luma_job.get('job_number')
            if completing:
                statuses_updated  += 1
                completed_updated += 1
                changes.append(f"#{ref} moved to Completed")
            # any field beyond the status we just added counts as a details write
            if len(patch) > (1 if completing else 0):
                details_updated += 1
                changes.append(f"#{ref} property details updated")
        except Exception:
            skipped_errors += 1

    if to_insert:
        sb_post('job_schedule', to_insert)

    return jsonify({
        'success':             True,
        'dates_updated':       dates_updated,
        'statuses_updated':    statuses_updated,
        'scheduled_matched':   scheduled_matched,
        'scheduled_unmatched': scheduled_unmatched,
        'skipped_no_date':     skipped_no_date,
        'skipped_errors':      skipped_errors,
        'writes_remaining':    writes_remaining,
        'details_updated':     details_updated,
        'completed_updated':   completed_updated,
        'tiles_created':       tiles_created,
        'tiles_updated':       tiles_updated,
        'changes':             changes[:60],
    })


@app.route('/api/monday/debug')
def api_monday_debug():
    """Show raw Monday property values vs Luma addresses for matching diagnosis."""
    items_q = '''
    query($bid: ID!) {
      boards(ids: [$bid]) {
        columns { id title type }
        items_page(limit: 50) {
          items {
            id
            name
            column_values { id text value }
          }
        }
      }
    }'''
    result = monday_query(items_q, {'bid': MONDAY_BOARD_ID})
    if 'errors' in result:
        return jsonify({'error': result['errors']}), 500

    board   = result['data']['boards'][0]
    columns = {c['id']: c['title'] for c in board['columns']}
    items   = board['items_page']['items']

    monday_props = []
    for item in items:
        row = {'name': item['name'], 'columns': {}}
        for cv in item['column_values']:
            if cv.get('text'):
                row['columns'][columns.get(cv['id'], cv['id'])] = cv['text']
        monday_props.append(row)

    luma_jobs = sb_get('jobs', 'select=id,job_ref,job_number,address&order=created_at.desc') or []
    luma_addrs = [{'job_ref': j.get('job_ref',''), 'address': j.get('address','')} for j in luma_jobs if j.get('address')]

    return jsonify({'monday_items': monday_props, 'luma_addresses': luma_addrs})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
