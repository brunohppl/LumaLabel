import os
import re
import base64
import tempfile
import json
import urllib.request
from io import BytesIO
from datetime import datetime

import pdfplumber
from flask import Flask, request, jsonify, Response
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

app = Flask(__name__)

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


# ── Colour cycle ──
COLOURS = [
    {'hex': '#E85C47', 'name': 'Red'},
    {'hex': '#4A9EE8', 'name': 'Blue'},
    {'hex': '#4CAF7D', 'name': 'Green'},
    {'hex': '#E8C547', 'name': 'Yellow'},
    {'hex': '#A04AE8', 'name': 'Purple'},
    {'hex': '#E87E47', 'name': 'Orange'},
    {'hex': '#E84A9E', 'name': 'Pink'},
]

# Persistent colour index stored in a simple file
COLOUR_INDEX_FILE = '/tmp/luma_colour_index.txt'

def get_next_colour():
    import random as _random
    try:
        with open(COLOUR_INDEX_FILE, 'r') as f:
            idx = int(f.read().strip())
    except:
        # File missing = fresh start after restart — pick random to avoid always starting on Red
        idx = _random.randint(0, len(COLOURS) - 1)
    next_idx = (idx + 1) % len(COLOURS)
    with open(COLOUR_INDEX_FILE, 'w') as f:
        f.write(str(next_idx))
    return COLOURS[idx]

# ── Room headers detected dynamically — no hardcoded list needed ──
SKIP_WORDS = [
    'Description','Quantity','EXTENSIONRATE','LUMADesignCoPtyLtd',
    'Unit223PerivaleSt','DARRAQLD4076','AUSTRALIA','ABN','Reference',
    'InvoiceDate','InvoiceNumber','PACKINGSLIP','96675056201',
    'EXTENSIONRATE',
]
SKIP_PATTERNS_WORDS = [
    r'^QU-',r'^\d+\.\d{2}$',r'^96\d+',r'p/week',r'weekhire',
    r'Unconditional',r'priortoend',r'collectionwill',r'notextending',
    r'extensionrate',r'Paymentof',r'Ifnotextending',r'Extensionrate',
]

def is_room_header(word, next_word):
    """Detect room headers dynamically.
    A word is a room header if:
    - Its significant letters are uppercase (ignoring ordinals like 2nd, 3rd)
    - The next word is a quantity like 1.00 or 2.00
    """
    import re as _re
    # Next word must be a quantity
    if not next_word or not _re.match(r'^\d+\.\d{2}$', next_word):
        return False
    # Strip leading ordinal prefix (2nd, 3rd etc.) before checking case
    stripped = _re.sub(r'^\d+(st|nd|rd|th)', '', word, flags=_re.I)
    letters_only = _re.sub(r'[^a-zA-Z]', '', stripped)
    if not letters_only:
        return False
    # Must be all uppercase
    return letters_only == letters_only.upper()

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

def parse_packing_list(pdf_bytes):
    meta = {'pl_number': '', 'job_number': '', 'address': '', 'stage_date': ''}
    items = []

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
    for idx, w in enumerate(all_words):
        if w in SKIP_WORDS: continue
        if any(re.search(p, w, re.I) for p in SKIP_PATTERNS_WORDS): continue
        # Dynamic room header detection: all-caps word followed by quantity
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

        # Artwork always gets 2 labels
        if re.search(r'\bartwork\b', description, re.I):
            qty = 2

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

    # ── Add extras to fill last label page + ~10 blank labels ──
    # Labels per page = 9 (3x3 Avery grid)
    per_page = 9
    current_count = len(items)
    remainder = current_count % per_page
    # Fill remainder of current page, then add one more full page worth
    extras_to_fill = (per_page - remainder) % per_page   # slots left on current page
    extras_count   = extras_to_fill + per_page            # fill page + one full extra page
    # Keep it between 9 and 18 (1-2 pages worth)
    extras_count = max(9, min(18, extras_count))

    for i in range(extras_count):
        items.append({
            'serial':      f'{serial:03d}',
            'room':        '',          # no location
            'description': '',          # blank
            'is_extra':    True,
        })
        serial += 1

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
def generate_labels(meta, items, colour):
    colour_hex  = colour['hex']
    date_txt    = format_date(meta['stage_date'])

    PAGE_W, PAGE_H = A4
    # Avery 89x62-R — exact dimensions from official Word template
    # Label: 62mm wide x 89mm tall, 3 cols x 3 rows
    # Both gaps equal at 4.99mm (283 DXA)
    MARGIN_X = 0.921 * cm  # 9.21mm left margin
    MARGIN_Y = 0.998 * cm  # 9.98mm top margin
    GAP_X    = 0.499 * cm  # 4.99mm horizontal gap
    GAP_Y    = 0.499 * cm  # 4.99mm vertical gap (same as horizontal)
    COLS     = 3
    ROWS     = 3
    LBL_W    = 6.20  * cm  # 62mm wide
    LBL_H    = 8.90  * cm  # 89mm tall

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
        w, h    = LBL_W, LBL_H
        pad     = 0.3 * cm
        split_x = x + w * 0.45
        rx      = split_x + pad
        rx_end  = x + w - pad
        rw      = rx_end - rx

        # Colour block
        c.setFillColor(C_ACCENT)
        c.roundRect(x, y, w * 0.45, h, 6, fill=1, stroke=0)
        c.rect(x + w * 0.45 - 6, y, 8, h, fill=1, stroke=0)

        # Invoice suffix number — bold white, rotated 90° anti-clockwise, centred on colour bar
        if inv_suffix:
            bar_w = w * 0.45
            bar_h = h
            # Find font size that fits along the bar height with padding
            inv_size = 42
            c.setFont('Helvetica-Bold', inv_size)
            while c.stringWidth(inv_suffix, 'Helvetica-Bold', inv_size) > bar_h - 0.4*cm and inv_size > 10:
                inv_size -= 1
            c.setFillColor(C_WHITE)
            c.setFont('Helvetica-Bold', inv_size)
            inv_w = c.stringWidth(inv_suffix, 'Helvetica-Bold', inv_size)
            # Centre of colour bar
            cx = x + bar_w / 2
            cy = y + bar_h / 2
            # Save state, translate to centre, rotate, draw centred
            c.saveState()
            c.translate(cx, cy)
            c.rotate(-90)  # 90° clockwise
            c.drawString(-inv_w / 2, -inv_size * 0.35, inv_suffix)
            c.restoreState()

        # White block
        c.setFillColor(C_WHITE)
        c.roundRect(x + w * 0.45, y, w * 0.55, h, 6, fill=1, stroke=0)
        c.rect(x + w * 0.45, y, 6, h, fill=1, stroke=0)

        # Border
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.8)
        c.roundRect(x, y, w, h, 6, fill=0, stroke=1)

        # DATE — top
        date_size = 17
        date_base = y + h - pad - 0.38 * cm
        c.setFillColor(C_INK)
        c.setFont('Helvetica-Bold', date_size)
        dw = c.stringWidth(date_txt, 'Helvetica-Bold', date_size)
        c.drawString(rx + (rw - dw) / 2, date_base - date_size * 0.35, date_txt)

        # Divider under date
        div_y = date_base - date_size * 0.38 - 0.15 * cm
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.4)
        c.line(rx, div_y, rx_end, div_y)

        # Middle zone: ITEM NUMBER + ID + ROOM stacked and centred
        addr_div_y = y + pad + 0.9 * cm
        mid_centre = (div_y - 0.1 * cm + addr_div_y + 0.1 * cm) / 2

        lbl_size  = 5.5
        id_size   = 34
        room_size = 14
        room_txt  = item['room'].upper()

        c.setFont('Helvetica-Bold', room_size)
        if c.stringWidth(room_txt, 'Helvetica-Bold', room_size) > rw:
            room_size = max(9, int(room_size * rw / c.stringWidth(room_txt, 'Helvetica-Bold', room_size)) - 1)

        gap     = 0.08 * cm
        block_h = (lbl_size * 0.4) + gap + (id_size * 0.75) + gap + (room_size * 0.75)
        room_y  = mid_centre - block_h / 2
        id_y    = room_y + room_size * 0.75 + gap
        lbl_y   = id_y + id_size * 0.75 + gap

        c.setFillColor(C_MUTED)
        c.setFont('Helvetica', lbl_size)
        lbl_w = c.stringWidth('ITEM NUMBER:', 'Helvetica', lbl_size)
        c.drawString(rx + (rw - lbl_w) / 2, lbl_y, 'ITEM NUMBER:')

        c.setFillColor(C_INK)
        c.setFont('Helvetica-Bold', id_size)
        id_txt = f'#{item["serial"]}'
        id_w   = c.stringWidth(id_txt, 'Helvetica-Bold', id_size)
        c.drawString(rx + (rw - id_w) / 2, id_y, id_txt)

        if room_txt:  # skip room text for blank extra labels
            c.setFont('Helvetica-Bold', room_size)
            rtw = c.stringWidth(room_txt, 'Helvetica-Bold', room_size)
            c.drawString(rx + (rw - rtw) / 2, room_y, room_txt)

        # Divider above address
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.3)
        c.line(rx, addr_div_y, rx_end, addr_div_y)

        # Address — two lines if needed
        addr      = meta['address']
        addr_size = 9
        c.setFillColor(C_INK)
        c.setFont('Helvetica-Bold', addr_size)

        if c.stringWidth(addr, 'Helvetica-Bold', addr_size) <= rw:
            aw = c.stringWidth(addr, 'Helvetica-Bold', addr_size)
            c.drawString(rx + (rw - aw) / 2, y + pad + 0.45 * cm, addr)
        else:
            parts   = addr.split(',', 1)
            line1   = parts[0].strip()
            line2   = parts[1].strip() if len(parts) > 1 else ''
            addr_y2 = y + pad + 0.20 * cm
            addr_y1 = addr_y2 + addr_size * 1.1
            for ln, ay in [(line1, addr_y1), (line2, addr_y2)]:
                lw = c.stringWidth(ln, 'Helvetica-Bold', addr_size)
                c.drawString(rx + (rw - lw) / 2, ay, ln)

    # Paginate
    per_page = COLS * ROWS
    total    = len(items)
    pages    = (total + per_page - 1) // per_page

    for pg in range(pages):
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        # Header
        c.setFillColor(HexColor('#1A1714'))
        c.setFont('Helvetica-Bold', 9)
        c.drawString(MARGIN_X, PAGE_H - MARGIN_Y + 0.4 * cm, 'LUMA')
        c.setFillColor(HexColor('#B8935A'))
        c.setFont('Helvetica-Oblique', 9)
        c.drawString(MARGIN_X + 1.3 * cm, PAGE_H - MARGIN_Y + 0.4 * cm, 'Design')
        c.setFillColor(HexColor('#1A1714'))
        c.setFont('Helvetica', 9)
        c.drawString(MARGIN_X + 2.7 * cm, PAGE_H - MARGIN_Y + 0.4 * cm, 'Co  —  Warehouse Labels')

        c.setFillColor(HexColor('#9A8F80'))
        c.setFont('Helvetica', 7)
        pg_txt = f'Page {pg+1} of {pages}   ·   {meta["job_number"]}   ·   {total} items total'
        pg_w   = c.stringWidth(pg_txt, 'Helvetica', 7)
        c.drawString(PAGE_W - MARGIN_X - pg_w, PAGE_H - MARGIN_Y + 0.4 * cm, pg_txt)

        for idx in range(per_page):
            item_idx = pg * per_page + idx
            if item_idx >= total: break
            col = idx % COLS
            row = ROWS - 1 - (idx // COLS)
            draw_label(
                MARGIN_X + col * (LBL_W + GAP_X),
                MARGIN_Y + row * (LBL_H + GAP_Y),
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
    left_data = [
        [Paragraph(f'<b>Job Ref:</b> {inv_suffix}', meta_style)],
        [Paragraph(f'<b>Address:</b> {meta["address"]}', meta_style)],
        [Paragraph(f'<b>Installation Date:</b> {meta["stage_date"]}', meta_style)],
        [Paragraph(f'<b>Total Items:</b> {len(items)}', meta_style)],
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
    col_widths = [1.1*cm, 6.5*cm, 7.2*cm, 1.8*cm, 1.9*cm]  # total ~18.5cm

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

        # Item rows for this room
        for i, item in enumerate(group_items):
            bg = colors.white if i % 2 == 0 else C_LIGHT
            rows.append([
                Paragraph(f'<b>{item["serial"]}</b>', ParagraphStyle('num',
                    fontName='Helvetica-Bold', fontSize=10,
                    textColor=C_ACCENT, alignment=TA_CENTER)),
                Paragraph(item.get('description', ''), cell_style),
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
        meta, items  = parse_packing_list(pdf_bytes)

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
        install_date = data.get('installDate')  # optional override from web app
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

        colour         = get_next_colour()
        pdf_bytes_out  = generate_labels(meta, items, colour)
        label_filename = f'LUMA_Labels_{meta["job_number"]}_{format_date(meta["stage_date"]).replace(" ", "")}.pdf'

        # Notify Slack (non-blocking — failure won't affect PDF delivery)
        notify_slack(meta, len(items), colour['name'], label_filename)

        # Send PDF directly to browser as a download — no third-party hosting needed
        return Response(
            pdf_bytes_out,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{label_filename}"',
                'X-Job-Number':  meta['job_number'],
                'X-Item-Count':  str(len(items)),
                'X-Colour':      colour['name'],
                'X-Address':     meta['address'],
                'X-Stage-Date':  meta['stage_date'],
            }
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
