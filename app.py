import os
import re
import base64
import tempfile
import json
import urllib.request
import urllib.parse
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

def save_job_to_db(meta, items, colour_name, job_owner='', is_transfer=False, transfer_from_job_id=None):
    """Save job and items to Supabase. Called after label generation."""
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
        # Delete existing job+items if re-generating
        existing = sb_get('jobs', f'job_number=eq.{meta["job_number"]}')
        if existing:
            job_id = existing[0]['id']
            sb_delete('items', f'job_id=eq.{job_id}')
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
    except Exception as e:
        pass  # Never let DB failure break label generation


# ── Slack notification ──
def notify_slack(meta, item_count, colour_name, label_filename):
    """Post a notification to Slack when labels are generated.
    Set SLACK_WEBHOOK_URL as an environment variable in Render."""
    webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
    if not webhook_url:
        return  # Silently skip if not configured

    colour_emoji = {
        'Red': '🔴', 'Blue': '🔵', 'Green': '🟢',
        'Yellow': '🟡', 'Purple': '🟣', 'Orange': '🟠', 'Pink': '🩷'
    }.get(colour_name, '🏷️')

    message = {
        'username': 'Luma Warehouse',
        'icon_emoji': ':package:',
        'blocks': [
            {
                'type': 'header',
                'text': {'type': 'plain_text', 'text': '🏷️  Labels Generated'}
            },
            {
                'type': 'section',
                'fields': [
                    {'type': 'mrkdwn', 'text': f'*Job*\n`{meta["job_number"]}`'},
                    {'type': 'mrkdwn', 'text': f'*Items*\n{item_count} labels'},
                    {'type': 'mrkdwn', 'text': f'*Address*\n{meta["address"]}'},
                    {'type': 'mrkdwn', 'text': f'*Date*\n{meta["stage_date"]}'},
                    {'type': 'mrkdwn', 'text': f'*Colour*\n{colour_emoji} {colour_name}'},
                    {'type': 'mrkdwn', 'text': f'*File*\n{label_filename}'},
                ]
            },
            {
                'type': 'context',
                'elements': [{'type': 'mrkdwn', 'text': 'Generated via LUMA Warehouse · lumalabel.onrender.com'}]
            }
        ]
    }

    try:
        data = json.dumps(message).encode('utf-8')
        req  = urllib.request.Request(
            webhook_url,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Never let Slack failure break label generation


def get_truck_eta(lat, lng, destination_address):
    """Look up driving time from (lat, lng) to destination_address using
    Google's Distance Matrix API. Set GOOGLE_MAPS_API_KEY as an
    environment variable in Render. The destination is passed as plain
    text — Google geocodes it server-side, so no separate geocoding step
    is needed here.

    Returns the human-readable duration string (e.g. "14 mins") on
    success, or None on any failure (missing key, network error, address
    not found, etc.) — callers should treat None as "couldn't calculate
    an ETA right now" and fail quietly, the same way notify_slack() does
    when its webhook isn't configured.
    """
    api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
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
        element = result['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            return None
        return element['duration']['text']
    except Exception:
        return None


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


# ════════════════════════════════════════════════
# GENERATE LABELS PDF
# ════════════════════════════════════════════════
def generate_labels(meta, items, colour, label_format=18):
    colour_hex  = colour['hex']
    date_txt    = format_date(meta['stage_date'])

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

        # ── Layout (18pp): ITEM NUMBER (upper) → ROOM (bottom) → ADDRESS + DATE (very bottom) ──
        ds  = 10    # date font
        asz = 5.5   # address font

        # Divider — near top, below where date used to be
        div_y = y + h - pad - ds * 1.2
        c.setStrokeColor(C_BORDER); c.setLineWidth(0.3)
        c.line(rx, div_y, rxe, div_y)

        # Address + Date on same line at very bottom
        addr = meta['address']
        c.setFillColor(C_MUTED); c.setFont('Helvetica', asz)
        # Date right-aligned, address left-aligned on same baseline
        date_sz = 8  # date font larger than address
        dw = c.stringWidth(date_txt, 'Helvetica-Bold', date_sz)
        # Truncate address if needed to leave room for date
        addr_max_w = rw - dw - 6
        addr_display = addr
        while addr_display and c.stringWidth(addr_display, 'Helvetica', asz) > addr_max_w:
            addr_display = addr_display[:-1]
        if addr_display != addr:
            addr_display = addr_display[:-1] + '…'
        baseline = y + pad
        c.setFont('Helvetica', asz)
        c.drawString(rx, baseline, addr_display)
        c.setFillColor(C_INK); c.setFont('Helvetica-Bold', date_sz)
        c.drawString(rxe - dw, baseline, date_txt)

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
        save_job_to_db(meta, items, colour['name'], job_owner, is_transfer, transfer_from_job_id)

        # Notify Slack (non-blocking — failure won't affect PDF delivery)
        notify_slack(meta, len(items), colour['name'], label_filename)

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
    # "Transferring to" is the inverse of "transfer from" — rather than
    # storing a second pointer on this job (which could fall out of sync
    # with the receiving job's transfer_from_job_id if either side is ever
    # edited independently), it's derived here by querying for any job
    # that names this one as its transfer source. This job has no record
    # of being a transfer source itself; it's purely a query result.
    transferring_to = sb_get('jobs', f'transfer_from_job_id=eq.{job_id}')
    return jsonify({'job': job[0], 'items': items, 'transferring_to': transferring_to})

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
    """Save job-level notes — independent of status.
    styling_notes: lead stylist's notes for other stylists picking the job.
    driver_notes: stylist's notes for the driver (e.g. transfer items, access info) — shown on both /stylist and /driver."""
    data    = request.get_json()
    payload = {}
    if 'styling_notes' in data: payload['styling_notes'] = data['styling_notes']
    if 'driver_notes'  in data: payload['driver_notes']  = data['driver_notes']
    result = sb_patch('jobs', f'id=eq.{job_id}', payload)
    return jsonify({'success': bool(result)})

@app.route('/api/jobs/<job_id>/eta', methods=['POST'])
def api_job_eta(job_id):
    """Calculate driving ETA from the driver's current position (sent by
    the browser via the Geolocation API, triggered when they tap the
    address on /driver/<job_id>) to this job's address, and save it on
    the job so it shows on the /jobs tile. See get_truck_eta() for the
    actual Distance Matrix call and why it fails silently rather than
    erroring — a missing API key or a network hiccup shouldn't block the
    driver from just opening Maps, which is the primary action here."""
    data = request.get_json()
    lat  = data.get('lat')
    lng  = data.get('lng')
    if lat is None or lng is None:
        return jsonify({'success': False, 'error': 'lat/lng required'}), 400

    job_rows = sb_get('jobs', f'id=eq.{job_id}')
    if not job_rows:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    address = job_rows[0].get('address', '')

    eta_text = get_truck_eta(lat, lng, address)
    if eta_text is None:
        # Couldn't calculate — leave any previous ETA untouched rather than
        # overwriting a good value with nothing just because this attempt failed.
        return jsonify({'success': False, 'eta_text': None})

    sb_patch('jobs', f'id=eq.{job_id}', {
        'eta_text': eta_text,
        'eta_calculated_at': datetime.utcnow().isoformat(),
    })
    return jsonify({'success': True, 'eta_text': eta_text})

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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
