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

# ── Supabase config ──
SUPABASE_URL  = os.environ.get('SUPABASE_URL', 'https://aqgxojawmohhogkhcxdb.supabase.co')
SUPABASE_KEY  = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFxZ3hvamF3bW9oaG9na2hjeGRiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzg3NDc5ODYsImV4cCI6MjA5NDMyMzk4Nn0.-2UOdGY52jDEmCmBBtQA2XEy6dVT8ZPA_AIPcM7RFX4')
SUPABASE_REST = SUPABASE_URL + '/rest/v1'

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

def sb_post(table, data):
    url     = f'{SUPABASE_REST}/{table}'
    payload = json.dumps(data).encode()
    req     = urllib.request.Request(url, data=payload, headers=sb_headers(), method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return None

def sb_patch(table, params, data):
    url     = f'{SUPABASE_REST}/{table}?{params}'
    payload = json.dumps(data).encode()
    hdrs    = {**sb_headers(), 'Prefer': 'return=representation'}
    req     = urllib.request.Request(url, data=payload, headers=hdrs, method='PATCH')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return None

def sb_delete(table, params):
    url = f'{SUPABASE_REST}/{table}?{params}'
    req = urllib.request.Request(url, headers=sb_headers(), method='DELETE')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True
    except Exception as e:
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
            'status':      'ready',
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
        params = urllib.parse.urlencode({
            'origins':      f'{lat},{lng}',
            'destinations': destination_address,
            'units':        'metric',
            'key':          api_key,
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

        duration_seconds = element['duration']['value']
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
RUNSHEET_VEHICLES = ['Marlin', 'Bruce', 'Nigel', 'Nemo', 'VUG']

RUNSHEET_STYLISTS = ['Addy', 'Montie', 'Delphine', 'India', 'Hayley']
RUNSHEET_DRIVERS  = ['Jo', 'Savio', 'Nick', 'Stefano', 'Yuri', 'Ayoub', 'Bruno', 'Phil']
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
RUNSHEET_DURATIONS = list(range(30, 270, 30))  # [30, 60, 90, ..., 240]


# ── Colour cycle — 14 maximally distinct colours ──
COLOURS = [
    {'hex': '#D62828', 'name': 'Red'},
    {'hex': '#1565C0', 'name': 'Blue'},
    {'hex': '#2E7D32', 'name': 'Green'},
    {'hex': '#F9A825', 'name': 'Yellow'},
    {'hex': '#6A0DAD', 'name': 'Purple'},
    {'hex': '#E65100', 'name': 'Orange'},
    {'hex': '#00838F', 'name': 'Teal'},
    {'hex': '#AD1457', 'name': 'Magenta'},
    {'hex': '#4E342E', 'name': 'Brown'},
    {'hex': '#558B2F', 'name': 'Olive'},
    {'hex': '#283593', 'name': 'Indigo'},
    {'hex': '#00ACC1', 'name': 'Cyan'},
    {'hex': '#F06292', 'name': 'Pink'},
    {'hex': '#757575', 'name': 'Grey'},
]

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
        match = next((c for c in COLOURS if c['name'] == manual_name), None)
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
    if not meta['stage_date']:
        import random
        meta['stage_date'] = (datetime.now() + __import__('datetime').timedelta(days=random.randint(3,14))).strftime('%-d %B %Y')

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
                              'description': f'{description} (Box)'})
                serial += 1
            continue

        # Artwork gets 1 label
        if re.search(r'\bartwork\b', description, re.I):
            qty = 1

        # Linen and cushion items get (Bag) suffix
        if re.search(r'\blinen\b|cushion', description, re.I):
            description = description + ' (Bag)'

        # Ensemble items always get exactly 3 labels: mattress + 2x bed frame
        if re.search(r'\bensemble\b', description, re.I):
            for suffix in ['(Mattress)', '(Bed Frame)', '(Bed Frame)']:
                items.append({'serial': f'{serial:03d}', 'room': current_room, 'description': f'{description} {suffix}'})
                serial += 1
            continue

        for _ in range(qty):
            items.append({'serial': f'{serial:03d}', 'room': current_room, 'description': description})
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
    """Parse a date string and return a prominent label-friendly format
    like 'WED 7TH JULY' for printing on the physical label itself.
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
    suffix = 'TH' if 11 <= day <= 13 else {1:'ST', 2:'ND', 3:'RD'}.get(day % 10, 'TH')
    return dt.strftime('%a').upper() + '-' + str(day) + suffix + ' ' + dt.strftime('%B').upper()


# ════════════════════════════════════════════════
# GENERATE LABELS PDF
# ════════════════════════════════════════════════
def generate_labels(meta, items, colour, label_format=18):
    colour_hex  = colour['hex']
    date_txt    = format_date_label(meta['stage_date'])

    PAGE_W, PAGE_H = A4

    # Avery 62x42-R — 18 per page, 3 cols x 6 rows
    # Equal spacing throughout: 6mm horizontal, 6.43mm vertical
    SX    = 6.00 * mm
    SY    = 6.43 * mm
    GX    = SX
    GY    = SY
    COLS  = 3
    ROWS  = 6
    LBL_W = 62 * mm
    LBL_H = 42 * mm

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    C_INK    = HexColor('#1A1714')
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
            c.setFillColor(C_WHITE)
            c.saveState()
            c.translate(x + bar_w / 2, y + h / 2)
            c.rotate(-90)
            c.drawString(-inv_w / 2, -inv_size * 0.35, inv_suffix)
            c.restoreState()

        # Right panel
        rx  = x + bar_w + pad
        rxe = x + w - pad
        rw  = rxe - rx

        # ── Layout (18pp): ITEM NUMBER (upper) → ROOM (bottom) → DATE (prominent) → ADDRESS (very bottom) ──
        asz = 5.5   # address font

        # Divider — near top, below where date used to be
        div_y = y + h - pad - 10 * 1.2
        c.setStrokeColor(C_BORDER); c.setLineWidth(0.3)
        c.line(rx, div_y, rxe, div_y)

        # DATE — large, bold, auto-sized to fill the available width between
        # divider and address line. "WED 7TH JULY" needs to be prominent
        # enough to read at a glance when labels are stacked.
        addr = meta['address']
        baseline_addr = y + pad
        date_area_h = div_y - baseline_addr - asz * 1.6 - 3  # space between divider and address
        # Auto-size: start large and shrink until it fits the panel width
        date_sz = 14
        while date_sz > 7 and c.stringWidth(date_txt, 'Helvetica-Bold', date_sz) > rw:
            date_sz -= 0.5
        date_y = baseline_addr + asz * 1.6 + 2  # sits just above the address line
        c.setFillColor(C_INK)
        c.setFont('Helvetica-Bold', date_sz)
        c.drawString(rx, date_y, date_txt)

        # Address — small, muted, at the very bottom
        c.setFillColor(C_MUTED); c.setFont('Helvetica', asz)
        addr_max_w = rw
        addr_display = addr
        while addr_display and c.stringWidth(addr_display, 'Helvetica', asz) > addr_max_w:
            addr_display = addr_display[:-1]
        if addr_display != addr:
            addr_display = addr_display[:-1] + '…'
        c.drawString(rx, baseline_addr, addr_display)

        # ── Fixed label zones — all positions fixed in points from label edges ──
        ROOM_FONT  = 7          # fixed font — no auto-sizing
        ROOM_Y     = y + 4      # shifted down
        ID_FONT    = 9
        ID_Y       = div_y - ID_FONT * 1.4 - 2  # just below divider

        # Item number
        id_txt = f'#{item["serial"]}'
        id_w   = c.stringWidth(id_txt, 'Helvetica-Bold', ID_FONT)
        c.setFillColor(C_MUTED); c.setFont('Helvetica-Bold', ID_FONT)
        c.drawString(rx + (rw - id_w) / 2, ID_Y, id_txt)

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


def generate_job_summary(job, items, room_notes=None):
    """Export a live snapshot of a job's current state from the stylist
    page — picked status, per-item notes, transfer markings, room notes,
    and job-level notes. Unlike generate_checklist() (which produces a
    blank form from a freshly parsed packing slip, meant to be filled in
    by hand), this reflects whatever has actually happened to the job so
    far in the database: what's been picked, what notes have been added,
    which items are tagged as transferring. Takes live Supabase rows
    directly rather than parser output, since that's what the stylist
    page itself is showing when someone asks to export it.
    """
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    room_notes = room_notes or {}
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
    C_GREEN  = HexColor('#4A7C59')
    C_BLUE   = HexColor('#4A7EB8')
    C_LIGHT  = HexColor('#F5F0E8')
    C_BORDER = HexColor('#D8CFBF')

    title_style = ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=20, textColor=C_INK, spaceAfter=2)
    sub_style   = ParagraphStyle('sub', fontName='Helvetica', fontSize=11, textColor=C_MUTED, spaceAfter=2)
    meta_style  = ParagraphStyle('meta', fontName='Helvetica-Bold', fontSize=10, textColor=C_INK, spaceAfter=0)
    cell_style  = ParagraphStyle('cell', fontName='Helvetica', fontSize=10, textColor=C_INK, leading=13)
    note_style  = ParagraphStyle('note', fontName='Helvetica-Oblique', fontSize=8.5, textColor=C_ACCENT, leading=11)
    hdr_style   = ParagraphStyle('hdr', fontName='Helvetica-Bold', fontSize=10, textColor=colors.white, alignment=TA_CENTER)
    room_section_style = ParagraphStyle('room_section', fontName='Helvetica-Bold', fontSize=11, textColor=colors.white)

    story = []
    story.append(Paragraph('LUMA <font color="#B8935A">Design</font> Co', title_style))
    story.append(Spacer(1, 14))
    story.append(Paragraph('Job Summary — current state', sub_style))
    story.append(Spacer(1, 8))

    # ── Job meta block ──
    pickable = [i for i in items if not i.get('is_extra')]
    picked_count = len([i for i in pickable if i.get('picked')])
    inv_suffix = re.sub(r'\D', '', job.get('job_number', '') or '')[-3:] or job.get('job_ref', '')

    meta_lines = [
        [Paragraph(f'<b>Job Ref:</b> {inv_suffix}', meta_style)],
        [Paragraph(f'<b>Address:</b> {job.get("address","")}', meta_style)],
        [Paragraph(f'<b>Installation Date:</b> {job.get("stage_date","")}', meta_style)],
        [Paragraph(f'<b>Job Owner:</b> {job.get("job_owner") or "—"}', meta_style)],
        [Paragraph(f'<b>Status:</b> {(job.get("status") or "").replace("_"," ").title() or "—"}', meta_style)],
        [Paragraph(f'<b>Picked:</b> {picked_count} / {len(pickable)} items', meta_style)],
    ]
    if job.get('is_transfer'):
        meta_lines.append([Paragraph('<b>Transfer From:</b> another job', meta_style)])
    meta_table = Table(meta_lines, colWidths=[18.5*cm])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), C_LIGHT),
        ('BOX',          (0,0), (-1,-1), 0.5, C_BORDER),
        ('TOPPADDING',   (0,0), (-1,-1), 6),
        ('BOTTOMPADDING',(0,0), (-1,-1), 6),
        ('LEFTPADDING',  (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # ── Job-level notes ──
    if job.get('styling_notes'):
        story.append(Paragraph(f'<b>Styling Notes:</b> {job["styling_notes"]}', cell_style))
        story.append(Spacer(1, 4))
    if job.get('driver_notes'):
        story.append(Paragraph(f'<b>Notes for Driver:</b> {job["driver_notes"]}', cell_style))
        story.append(Spacer(1, 4))
    story.append(Spacer(1, 6))

    # ── Table: # | Item | Status | Notes ──
    col_widths = [2.4*cm, 8.5*cm, 2.5*cm, 5.1*cm]
    headers = [
        Paragraph('#', hdr_style),
        Paragraph('Item', hdr_style),
        Paragraph('Status', hdr_style),
        Paragraph('Notes', hdr_style),
    ]
    rows = [headers]
    style_cmds = [
        ('BACKGROUND',   (0,0), (-1,0), C_INK),
        ('FONTNAME',     (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0,0), (-1,0), 10),
        ('TOPPADDING',   (0,0), (-1,0), 9),
        ('BOTTOMPADDING',(0,0), (-1,0), 9),
        ('LEFTPADDING',  (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN',        (0,0), (0,-1), 'CENTER'),
        ('ALIGN',        (2,0), (2,-1), 'CENTER'),
        ('GRID',         (0,0), (-1,-1), 0.4, C_BORDER),
        ('LINEBELOW',    (0,0), (-1,0), 1.0, C_INK),
    ]
    data_row_idx = 1

    # Group live items by room, preserving the order rooms first appear in
    rooms_in_order = []
    by_room = {}
    for item in pickable:
        r = item.get('room') or ''
        if r not in by_room:
            by_room[r] = []
            rooms_in_order.append(r)
        by_room[r].append(item)

    for room in rooms_in_order:
        room_items = by_room[room]

        section_cells = [Paragraph((room or 'Uncategorised').upper(), room_section_style), '', '', '']
        notes_for_room = room_notes.get(room) or []
        rows.append(section_cells)
        style_cmds += [
            ('BACKGROUND',   (0, data_row_idx), (-1, data_row_idx), C_ACCENT),
            ('SPAN',         (0, data_row_idx), (-1, data_row_idx)),
            ('TOPPADDING',   (0, data_row_idx), (-1, data_row_idx), 7),
            ('BOTTOMPADDING',(0, data_row_idx), (-1, data_row_idx), 7),
        ]
        data_row_idx += 1

        if notes_for_room:
            rows.append([Paragraph(' · '.join(notes_for_room), note_style), '', '', ''])
            style_cmds += [
                ('BACKGROUND', (0, data_row_idx), (-1, data_row_idx), C_LIGHT),
                ('SPAN',       (0, data_row_idx), (-1, data_row_idx)),
                ('TOPPADDING', (0, data_row_idx), (-1, data_row_idx), 5),
                ('BOTTOMPADDING', (0, data_row_idx), (-1, data_row_idx), 5),
            ]
            data_row_idx += 1

        # Group consecutive identical descriptions, same as the checklist —
        # but split on transfer-status boundaries too, since merging a
        # transferring item with a non-transferring one of the same
        # description would hide which is which.
        grouped = []
        i = 0
        while i < len(room_items):
            item = room_items[i]
            desc = item.get('description', '')
            is_transfer_item = bool(item.get('is_transfer_item'))
            not_transferring  = bool(item.get('not_transferring'))
            j = i + 1
            while (j < len(room_items)
                   and room_items[j].get('description', '') == desc
                   and bool(room_items[j].get('is_transfer_item')) == is_transfer_item
                   and bool(room_items[j].get('not_transferring')) == not_transferring):
                j += 1
            chunk = room_items[i:j]
            grouped.append({
                'count': j - i,
                'description': desc,
                'first_serial': item['serial'],
                'last_serial': room_items[j-1]['serial'],
                'all_picked': all(x.get('picked') for x in chunk),
                'any_picked': any(x.get('picked') for x in chunk),
                'is_transfer_item': is_transfer_item,
                'not_transferring': not_transferring,
                'notes': item.get('notes') or '',
            })
            i = j

        for i, grp in enumerate(grouped):
            bg = colors.white if i % 2 == 0 else C_LIGHT
            serial_txt = f'#{grp["first_serial"]}' if grp['count'] == 1 else f'#{grp["first_serial"]}–#{grp["last_serial"]}'
            item_txt = f'{grp["count"]}×  {grp["description"]}' if grp['count'] > 1 else grp['description']

            if grp['is_transfer_item']:
                status_txt, status_colour = 'Transfer', C_BLUE
            elif grp['not_transferring']:
                status_txt, status_colour = 'Not Transferring', C_ACCENT
            elif grp['all_picked']:
                status_txt, status_colour = 'Picked', C_GREEN
            elif grp['any_picked']:
                status_txt, status_colour = 'Partial', C_ACCENT
            else:
                status_txt, status_colour = 'Not Picked', C_MUTED

            serial_style = ParagraphStyle('num', fontName='Helvetica-Bold', fontSize=10, textColor=C_ACCENT, alignment=TA_CENTER)
            status_style = ParagraphStyle('status', fontName='Helvetica-Bold', fontSize=8.5, textColor=status_colour, alignment=TA_CENTER)

            rows.append([
                Paragraph(serial_txt, serial_style),
                Paragraph(item_txt, cell_style),
                Paragraph(status_txt, status_style),
                Paragraph(grp['notes'], note_style) if grp['notes'] else Paragraph('', cell_style),
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

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f'Exported from LUMA Warehouse · {datetime.now().strftime("%-d %b %Y, %-I:%M%p")}',
        ParagraphStyle('footer', fontName='Helvetica', fontSize=7, textColor=C_MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# ════════════════════════════════════════════════
# API ROUTES
# ════════════════════════════════════════════════
@app.route('/', methods=['GET'])
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

        pdf_bytes    = base64.b64decode(pdf_base64)
        install_date = data.get('installDate')
        job_owner    = data.get('jobOwner', '')
        label_format = int(data.get('labelFormat', 18))  # 9 or 18 per page
        meta, items  = parse_packing_list(pdf_bytes)
        meta['job_owner'] = job_owner

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

        pdf_bytes    = base64.b64decode(pdf_base64)
        install_date = data.get('installDate')
        job_owner    = data.get('jobOwner', '')
        label_format = int(data.get('labelFormat', 18))  # 9 or 18 per page
        colour_name  = data.get('colourName')  # manual colour choice, or None for Auto
        is_transfer  = bool(data.get('isTransfer', False))
        transfer_from_job_id = data.get('transferFromJobId') or None
        meta, items  = parse_packing_list(pdf_bytes)

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

    pdf_bytes = generate_job_summary(job, items, room_notes)
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
    # "Returned" is a terminal action — once the truck is back at the warehouse,
    # the job is done. Auto-archive it immediately rather than requiring a
    # separate manual archive step.
    if status == 'returned':
        status = 'archived'
    payload = {'status': status}
    if 'truck' in data: payload['truck'] = data['truck']
    result = sb_patch('jobs', f'id=eq.{job_id}', payload)
    return jsonify({'success': bool(result)})

@app.route('/api/jobs/<job_id>/notes', methods=['PATCH'])
def api_job_notes(job_id):
    """Save job-level notes and accessory tub count — independent of status.
    styling_notes: lead stylist's notes for other stylists picking the job.
    driver_notes: stylist's notes for the driver — shown on both /stylist and /driver.
    accessory_tubs: number of accessory tubs needed — set by stylist, shown on driver."""
    data    = request.get_json()
    payload = {}
    if 'styling_notes'  in data: payload['styling_notes']  = data['styling_notes']
    if 'driver_notes'   in data: payload['driver_notes']   = data['driver_notes']
    if 'accessory_tubs' in data:
        v = data['accessory_tubs']
        payload['accessory_tubs'] = int(v) if v not in (None, '', 0) else None
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


def seed_two_day_schedule(job_id, main_date_str, main_type, items=None):
    """Seed the standard two-day schedule for a job:
    - Load day  (day before main_date): to_load, 13:30–15:30 (120 min)
    - Main day  (main_date):            main_type, 07:30–10:00 (150 min)

    If items are provided, smart vehicle assignment is applied:
    bedroom count → Nemo (1-2 bed), Nigel (3 bed), Nigel+Nemo (4+ bed).
    Vehicle is null (unscheduled strip) when bedroom count can't be determined.

    For to_load type, only seeds the single load day.
    Re-upload or date change: clears existing entries first."""
    from datetime import datetime as _dt, timedelta
    sb_delete('job_schedule', f'job_id=eq.{job_id}')

    try:
        main_dt   = _dt.strptime(main_date_str, '%Y-%m-%d')
        load_dt   = main_dt - timedelta(days=1)
        load_date = load_dt.strftime('%Y-%m-%d')
    except ValueError:
        return

    vehicles = vehicles_for_job(items) if items else []

    if vehicles:
        # Create one load entry per vehicle
        for v in vehicles:
            sb_post('job_schedule', {
                'job_id':     job_id,
                'date':       load_date,
                'vehicle':    v,
                'start_time': '13:30',
                'duration':   120,
                'notes':      None,
            })
        if main_type != 'to_load':
            for v in vehicles:
                sb_post('job_schedule', {
                    'job_id':     job_id,
                    'date':       main_date_str,
                    'vehicle':    v,
                    'start_time': '07:30',
                    'duration':   150,
                    'notes':      None,
                })
    else:
        # No bedroom data — create unassigned entries (show in unscheduled strip)
        sb_post('job_schedule', {
            'job_id':     job_id,
            'date':       load_date,
            'vehicle':    None,
            'start_time': '13:30',
            'duration':   120,
            'notes':      None,
        })
        if main_type != 'to_load':
            sb_post('job_schedule', {
                'job_id':     job_id,
                'date':       main_date_str,
                'vehicle':    None,
                'start_time': '07:30',
                'duration':   150,
                'notes':      None,
            })

    # Keep jobs.runsheet_date on the main date for backward compat
    sb_patch('jobs', f'id=eq.{job_id}', {
        'runsheet_date': main_date_str,
        'runsheet_type': main_type,
    })


@app.route('/api/jobs/<job_id>/runsheet', methods=['PATCH'])
def api_job_runsheet(job_id):
    """Set or clear the job's runsheet schedule.

    When setting a date + type, auto-seeds the standard two-day schedule:
    - Install/Pickup: load entry on the day before + main entry on the date
    - To Load: just the single load entry on that date

    Clearing (runsheet_date: null) deletes all job_schedule rows."""
    data = request.get_json()
    runsheet_date = data.get('runsheet_date')
    runsheet_type = data.get('runsheet_type')

    if runsheet_date is not None and runsheet_type not in ('install', 'pickup', 'to_load'):
        return jsonify({'success': False,
                        'error': 'runsheet_type must be install, pickup, or to_load'}), 400

    if runsheet_date:
        # Fetch items so bedroom count can drive smart vehicle assignment
        items = sb_get('items', f'job_id=eq.{job_id}') or []
        seed_two_day_schedule(job_id, runsheet_date, runsheet_type, items=items)
    else:
        sb_delete('job_schedule', f'job_id=eq.{job_id}')
        sb_patch('jobs', f'id=eq.{job_id}', {
            'runsheet_date': None,
            'runsheet_type': None,
        })

    return jsonify({'success': True})


@app.route('/api/runsheet/<date_str>', methods=['GET'])
def api_runsheet_day(date_str):
    """Jobs + schedule entries + crew + tasks for a specific date.

    Schedule entries are now queried by job_schedule.date rather than
    jobs.runsheet_date — this is what allows a single job to appear on
    two different days (load day + install day). We collect all jobs
    that have any schedule entry on this date, then return those jobs
    and their entries together."""
    # Find all schedule entries for this date
    schedule = sb_get('job_schedule', f'date=eq.{date_str}&order=start_time.asc,created_at.asc') or []

    # Derive the unique job IDs from those entries
    job_ids = list({e['job_id'] for e in schedule if e.get('job_id')})

    # Also include jobs whose legacy runsheet_date matches (backward compat
    # for jobs scheduled before the date column was added to job_schedule)
    legacy_jobs = sb_get('jobs', f'runsheet_date=eq.{date_str}') or []
    legacy_ids  = [j['id'] for j in legacy_jobs]
    all_ids     = list({*job_ids, *legacy_ids})

    jobs = []
    if all_ids:
        ids_str = ','.join(all_ids)
        jobs    = sb_get('jobs', f'id=in.({ids_str})') or []

    crew  = sb_get('vehicle_day_crew', f'date=eq.{date_str}') or []
    tasks = sb_get('runsheet_tasks',   f'date=eq.{date_str}&order=start_time.asc') or []

    return jsonify({'jobs': jobs, 'schedule': schedule,
                    'crew': crew, 'tasks': tasks})


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
    if vehicle != 'ALL' and vehicle not in RUNSHEET_VEHICLES:
        return jsonify({'success': False, 'error': f'Unknown vehicle: {vehicle}'}), 400
    if start_time is not None and start_time not in RUNSHEET_TIME_SLOTS:
        return jsonify({'success': False, 'error': 'Invalid start_time'}), 400
    if duration is not None and duration not in RUNSHEET_DURATIONS:
        return jsonify({'success': False, 'error': 'Invalid duration'}), 400

    result = sb_post('runsheet_tasks', {
        'vehicle': vehicle, 'date': date_str, 'title': title,
        'notes': notes, 'start_time': start_time, 'duration': duration,
    })
    return jsonify({'success': bool(result), 'task': result[0] if result else None})


@app.route('/api/tasks/<task_id>', methods=['PATCH'])
def api_task_update(task_id):
    """Edit a task. Body: any of {title, notes, vehicle, start_time, duration}"""
    data    = request.get_json()
    payload = {}
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
        if v != 'ALL' and v not in RUNSHEET_VEHICLES:
            return jsonify({'success': False, 'error': f'Unknown vehicle: {v}'}), 400
        payload['vehicle'] = v
    result = sb_patch('runsheet_tasks', f'id=eq.{task_id}', payload)
    return jsonify({'success': bool(result)})


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
    """Add a vehicle assignment. Body: {vehicle?, date?, start_time?, duration?, notes?}
    vehicle may be null for auto-seeded entries (shown in unscheduled strip)."""
    data       = request.get_json()
    vehicle    = data.get('vehicle')
    date_str   = data.get('date')
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
        'start_time': start_time, 'duration': duration, 'notes': notes,
    })
    return jsonify({'success': bool(result), 'row': result[0] if result else None})


@app.route('/api/schedule/<entry_id>', methods=['PATCH'])
def api_schedule_update(entry_id):
    """Edit a vehicle assignment. Body: any of {vehicle, date, start_time, duration, notes}"""
    data    = request.get_json()
    payload = {}
    if 'vehicle' in data:
        if data['vehicle'] is not None and data['vehicle'] not in RUNSHEET_VEHICLES:
            return jsonify({'success': False, 'error': 'Unknown vehicle'}), 400
        payload['vehicle'] = data['vehicle']
    if 'date'       in data: payload['date']       = data['date']
    if 'start_time' in data:
        if data['start_time'] is not None and data['start_time'] not in RUNSHEET_TIME_SLOTS:
            return jsonify({'success': False, 'error': 'Invalid start_time'}), 400
        payload['start_time'] = data['start_time']
    if 'duration'   in data:
        if data['duration'] is not None and data['duration'] not in RUNSHEET_DURATIONS:
            return jsonify({'success': False, 'error': 'Invalid duration'}), 400
        payload['duration'] = data['duration']
    if 'notes'      in data: payload['notes'] = data['notes'] or None
    result = sb_patch('job_schedule', f'id=eq.{entry_id}', payload)
    return jsonify({'success': bool(result)})


@app.route('/api/schedule/<entry_id>', methods=['DELETE'])
def api_schedule_delete(entry_id):
    result = sb_delete('job_schedule', f'id=eq.{entry_id}')
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

@app.route('/api/items/<item_id>/photos', methods=['POST'])
def api_item_photos_add(item_id):
    """Record a new photo URL for an item. The actual file upload goes
    direct from the browser to Supabase Storage — this just saves the URL.
    Also updates items.photo_url to the new URL so the driver interface
    always shows the most recently added photo."""
    data = request.get_json()
    url  = data.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'url required'}), 400
    result = sb_post('item_photos', {'item_id': item_id, 'url': url})
    # Keep items.photo_url in sync as the latest photo
    sb_patch('items', f'id=eq.{item_id}', {'photo_url': url})
    return jsonify({'success': bool(result), 'photo': result[0] if result else None})

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
