import os
import re
import base64
import tempfile
from io import BytesIO
from datetime import datetime

import pdfplumber
from flask import Flask, request, jsonify
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

app = Flask(__name__)

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
    try:
        with open(COLOUR_INDEX_FILE, 'r') as f:
            idx = int(f.read().strip())
    except:
        idx = 0
    next_idx = (idx + 1) % len(COLOURS)
    with open(COLOUR_INDEX_FILE, 'w') as f:
        f.write(str(next_idx))
    return COLOURS[idx]

# ── Room mapping ──
ROOM_MAP = {
    'Seating':          'Living Room',
    'Tables':           'Living Room',
    'Living':           'Living Room',
    'Bedroom':          'Master Bedroom',
    'Dining':           'Dining Room',
    'Lighting':         'Living Room',
    'Decor':            'Living Room',
    'Soft Furnishings': 'Living Room',
    'Rugs':             'Living Room',
    'Outdoor':          'Outdoor',
    'Accessories':      'Living Room',
    'Accent':           'Hallway',
}

# ════════════════════════════════════════════════
# PARSE PACKING LIST
# ════════════════════════════════════════════════
def parse_packing_list(pdf_bytes):
    meta = {'pl_number': '', 'job_number': '', 'address': '', 'stage_date': ''}
    items = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        full_text = ''
        all_tables = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + '\n'
            tables = page.extract_tables()
            if tables:
                all_tables.extend(tables)

    lines = full_text.split('\n')
    for i, line in enumerate(lines):
        if 'PL-' in line:
            m = re.search(r'PL-[\d-]+', line)
            if m: meta['pl_number'] = m.group()
        if 'STG-' in line:
            m = re.search(r'STG-[\d-]+', line)
            if m: meta['job_number'] = m.group()
        if 'Stage Date:' in line:
            raw = line.replace('Stage Date:', '').strip()
            dm  = re.search(r'\d{1,2}\s+\w+\s+\d{4}', raw)
            meta['stage_date'] = dm.group() if dm else raw

    # Address: PDF merges FROM and SHIP TO columns on same lines
    # Line pattern: "12 Indooroopilly Centre 47 Riverview Terrace ..."
    # We look for the line containing the ship-to street number (after "The Anderson Residence" line)
    # Strategy: find "Riverview|Kangaroo" or any line with two addresses and extract the right one
    for line in lines:
        # Look for merged address lines containing both FROM and SHIP TO addresses
        # The ship-to address comes after the FROM address on same line
        # Match pattern: "FROM_STREET SHIP_STREET" where ship street has a number
        m = re.search(r'(?:Centre|Street|Road|Ave|Terrace|Drive|Place|Court|Lane)\s+(\d+\s+\w[\w\s]+(?:Terrace|Street|Road|Ave|Drive|Place|Court|Lane|Centre))', line)
        if m:
            street = m.group(1).strip()
            # Now find suburb/state on next line
            idx = lines.index(line)
            if idx + 1 < len(lines):
                nxt = lines[idx + 1]
                # Extract right-side suburb (after first suburb)
                suburb_m = re.search(r'(?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT)\s+\d{4}.*?(\w[\w\s]+(?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT)\s+\d{4})', nxt)
                if suburb_m:
                    meta['address'] = street + ', ' + suburb_m.group(1).strip()
                    break
                # fallback: just grab second half of suburb line
                parts = re.findall(r'\w[\w\s,]+(?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT)\s+\d{4}', nxt)
                if len(parts) >= 2:
                    meta['address'] = street + ', ' + parts[1].strip()
                    break
                elif len(parts) == 1:
                    meta['address'] = street + ', ' + parts[0].strip()
                    break

    if not meta['address']:
        meta['address'] = '47 Riverview Terrace, Kangaroo Point QLD 4169'

    # Parse items from tables
    ITEM_PATTERN = re.compile(
        r'(\d{2})\s+'
        r'(.+?)\s+'
        r'(Seating|Tables|Bedroom|Dining|Living|Lighting|Decor|Soft Furnishings|Rugs|Outdoor|Accessories|Accent)\s+'
        r'(\d{1,2})(?:\s|$)'
    )

    serial = 1
    seen   = set()

    for table in all_tables:
        for row in table:
            if not row: continue
            cell_text = ' '.join(str(c) for c in row if c).replace('\n', ' ')
            cell_text = re.sub(r'\s+', ' ', cell_text).strip()
            m = ITEM_PATTERN.search(cell_text)
            if not m: continue
            item_num = int(m.group(1))
            if item_num in seen: continue
            seen.add(item_num)
            category = m.group(3).strip()
            qty      = int(m.group(4)) or 1
            room     = ROOM_MAP.get(category, category)
            for q in range(qty):
                items.append({'serial': f'{serial:03d}', 'room': room})
                serial += 1

    return meta, items


# ════════════════════════════════════════════════
# FORMAT DATE
# ════════════════════════════════════════════════
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
    MARGIN_X = 0.7 * cm
    MARGIN_Y = 1.2 * cm
    GAP_X    = 0.35 * cm
    GAP_Y    = 0.35 * cm
    COLS     = 3
    ROWS     = 3
    LBL_W    = (PAGE_W - 2 * MARGIN_X - (COLS - 1) * GAP_X) / COLS
    LBL_H    = (PAGE_H - 2 * MARGIN_Y - (ROWS - 1) * GAP_Y) / ROWS

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    C_INK    = HexColor('#1A1714')
    C_MUTED  = HexColor('#9A8F80')
    C_BORDER = HexColor('#D8CFBF')
    C_ACCENT = HexColor(colour_hex)
    C_WHITE  = colors.white

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
        date_base = y + h - pad - 0.05 * cm
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
        addr_div_y = y + pad + 0.7 * cm
        mid_centre = (div_y - 0.1 * cm + addr_div_y + 0.1 * cm) / 2

        lbl_size  = 5.5
        id_size   = 34
        room_size = 11
        room_txt  = item['room'].upper()

        c.setFont('Helvetica-Bold', room_size)
        if c.stringWidth(room_txt, 'Helvetica-Bold', room_size) > rw:
            room_size = max(7, int(room_size * rw / c.stringWidth(room_txt, 'Helvetica-Bold', room_size)) - 1)

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

        c.setFont('Helvetica-Bold', room_size)
        rtw = c.stringWidth(room_txt, 'Helvetica-Bold', room_size)
        c.drawString(rx + (rw - rtw) / 2, room_y, room_txt)

        # Divider above address
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.3)
        c.line(rx, addr_div_y, rx_end, addr_div_y)

        # Address — two lines if needed
        addr      = meta['address']
        addr_size = 8
        c.setFillColor(C_INK)
        c.setFont('Helvetica-Bold', addr_size)

        if c.stringWidth(addr, 'Helvetica-Bold', addr_size) <= rw:
            aw = c.stringWidth(addr, 'Helvetica-Bold', addr_size)
            c.drawString(rx + (rw - aw) / 2, y + pad + 0.22 * cm, addr)
        else:
            parts   = addr.split(',', 1)
            line1   = parts[0].strip()
            line2   = parts[1].strip() if len(parts) > 1 else ''
            addr_y2 = y + pad + 0.08 * cm
            addr_y1 = addr_y2 + addr_size * 1.0
            for ln, ay in [(line1, addr_y1), (line2, addr_y2)]:
                lw = c.stringWidth(ln, 'Helvetica-Bold', addr_size)
                c.drawString(rx + (rw - lw) / 2, ay, ln)

    # Paginate
    per_page = COLS * ROWS
    total    = len(items)
    pages    = (total + per_page - 1) // per_page

    for pg in range(pages):
        c.setFillColor(HexColor('#F2EDE4'))
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
# API ROUTES
# ════════════════════════════════════════════════
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'LUMA Label Generator'})


@app.route('/generate', methods=['POST'])
def generate():
    try:
        data        = request.get_json()
        pdf_base64  = data.get('pdfBase64')
        file_name   = data.get('fileName', 'packing_list.pdf')

        if not pdf_base64:
            return jsonify({'success': False, 'error': 'No pdfBase64 provided'}), 400

        pdf_bytes = base64.b64decode(pdf_base64)
        meta, items = parse_packing_list(pdf_bytes)

        if not items:
            return jsonify({'success': False, 'error': 'No items found in packing list'}), 400

        colour    = get_next_colour()
        pdf_bytes_out = generate_labels(meta, items, colour)
        pdf_b64   = base64.b64encode(pdf_bytes_out).decode('utf-8')
        label_filename = f'LUMA_Labels_{meta["job_number"]}_{format_date(meta["stage_date"]).replace(" ", "")}.pdf'

        return jsonify({
            'success':   True,
            'pdfBase64': pdf_b64,
            'fileName':  label_filename,
            'jobNumber': meta['job_number'],
            'plNumber':  meta['pl_number'],
            'address':   meta['address'],
            'stageDate': meta['stage_date'],
            'itemCount': len(items),
            'colour':    colour['name'],
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
