"""
Deliveries — Programma schedule import & delivery checking.

Self-contained Flask Blueprint for a separate LUMA department. Deliberately
isolated from the warehouse app:
  - all routes live under /deliveries and /api/deliveries
  - all Supabase tables are namespaced delivery_*
  - no imports from app.py, no shared state
  - the xlsx parser uses ONLY the Python standard library (zipfile + xml),
    so nothing needs adding to requirements.txt and the live app's build
    cannot be affected

An .xlsx file is a zip archive of XML. Cell text is usually stored in a
shared-string table and referenced by index, which is why we read
xl/sharedStrings.xml first and then resolve each cell against it.
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from flask import Blueprint, request, jsonify, render_template

deliveries_bp = Blueprint('deliveries', __name__)

# OOXML namespace used by every sheet/sharedStrings document
_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


# ─────────────────────────── xlsx reading ───────────────────────────

def _col_letter(cell_ref):
    """'BC12' -> 'BC'. Cell refs carry the column, which matters because
    empty cells are omitted from the XML entirely — so positional order
    is unreliable and we must map by column letter."""
    return re.match(r'([A-Z]+)', cell_ref or '').group(1) if cell_ref else ''


def _shared_strings(zf):
    """Read the workbook's shared-string table into a list."""
    try:
        xml = zf.read('xl/sharedStrings.xml')
    except KeyError:
        return []
    root = ET.fromstring(xml)
    out = []
    for si in root.findall('m:si', _NS):
        # A string may be split across multiple runs (<r><t>..</t></r>)
        out.append(''.join(t.text or '' for t in si.iter(
            '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')))
    return out


def _first_sheet_path(zf):
    """Resolve the first worksheet's path from the workbook rels."""
    names = [n for n in zf.namelist()
             if n.startswith('xl/worksheets/') and n.endswith('.xml')]
    if not names:
        raise ValueError('No worksheet found in this file.')
    # sheet1.xml sorts first naturally in the common case
    return sorted(names)[0]


def read_xlsx_rows(file_bytes):
    """Yield each row as a dict of {column_letter: cell_text}."""
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        strings = _shared_strings(zf)
        sheet_xml = zf.read(_first_sheet_path(zf))

    root = ET.fromstring(sheet_xml)
    sheet_data = root.find('m:sheetData', _NS)
    if sheet_data is None:
        return

    for row in sheet_data.findall('m:row', _NS):
        cells = {}
        for c in row.findall('m:c', _NS):
            ref = c.get('r')
            ctype = c.get('t')
            val = ''
            if ctype == 'inlineStr':
                is_el = c.find('m:is', _NS)
                if is_el is not None:
                    val = ''.join(t.text or '' for t in is_el.iter(
                        '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'))
            else:
                v = c.find('m:v', _NS)
                if v is not None and v.text is not None:
                    if ctype == 's':  # shared-string index
                        try:
                            val = strings[int(v.text)]
                        except (ValueError, IndexError):
                            val = ''
                    else:
                        val = v.text
            val = (val or '').replace('_x000D_', ' ').strip()
            if val:
                cells[_col_letter(ref)] = val
        yield cells


# ──────────────────────── Programma parsing ────────────────────────

# Header cells we care about -> the key we store them under.
# Matched case-insensitively against the repeated header row.
_WANTED = {
    'product description': 'item_label',
    'product name':        'product_name',
    'brand':               'brand',
    'sku':                 'sku',
    'doc code':            'doc_code',
    'colour':              'colour',
    'finish':              'finish',
    'material':            'material',
    'width':               'width',
    'length':              'length',
    'height':              'height',
    'depth':               'depth',
    'lead time':           'lead_time',
    'quantity':            'qty_expected',
    'rrp':                 'rrp',
    'status':              'programma_status',
    'supplier company name': 'supplier',
    'website url':         'url',
    'important information': 'important_info',
    'notes':               'notes',
}


def _clean(v):
    """Programma writes '-' for empty. Normalise that to None."""
    if v is None:
        return None
    v = v.strip()
    return None if v in ('', '-') else v


def parse_programma_xlsx(file_bytes):
    """Parse a Programma schedule export into structured lines.

    The export is report-style: a 'Section : <room>' line, then a full
    header row, then that section's data rows — repeating per section.
    Returns (lines, meta)."""
    lines = []
    meta = {'project': None, 'schedule': None, 'sections': []}
    colmap = {}          # column letter -> our field key
    current_section = None

    for cells in read_xlsx_rows(file_bytes):
        if not cells:
            continue
        joined = ' '.join(cells.values())

        # Document header lines
        if meta['project'] is None and joined.startswith('Project :'):
            meta['project'] = joined.split(':', 1)[1].strip()
            continue
        if meta['schedule'] is None and joined.startswith('Schedule :'):
            meta['schedule'] = joined.split(':', 1)[1].strip()
            continue

        # Section marker
        if joined.startswith('Section :'):
            current_section = joined.split(':', 1)[1].strip()
            if current_section and current_section not in meta['sections']:
                meta['sections'].append(current_section)
            continue

        # Header row — rebuild the column map (it repeats per section and
        # we must not assume the column order is identical every time)
        lowered = {k: v.strip().lower() for k, v in cells.items()}
        if 'product description' in lowered.values() and 'sku' in lowered.values():
            colmap = {col: _WANTED[name]
                      for col, name in lowered.items() if name in _WANTED}
            continue

        if not colmap:
            continue  # data before any header — skip

        row = {field: _clean(cells.get(col)) for col, field in colmap.items()}

        # A real deliverable has at least a product name, brand, or SKU.
        # Lines with none of those are budgets/services (styling budget,
        # art hanging, installation, delivery cost) — flagged, not dropped,
        # so the user can see they were recognised and excluded.
        is_service = not any([row.get('product_name'),
                              row.get('brand'),
                              row.get('sku')])

        if not row.get('item_label') and is_service:
            continue  # genuinely blank row

        try:
            qty = int(float(row.get('qty_expected') or 1))
        except (ValueError, TypeError):
            qty = 1

        lines.append({
            'section':          current_section,
            'item_label':       row.get('item_label'),
            'product_name':     row.get('product_name'),
            'brand':            row.get('brand'),
            'sku':              row.get('sku'),
            'doc_code':         row.get('doc_code'),
            'colour':           row.get('colour'),
            'finish':           row.get('finish'),
            'material':         row.get('material'),
            'dimensions':       ' × '.join(
                                    f'{k}{row.get(k)}'
                                    for k in ('width', 'length', 'height', 'depth')
                                    if row.get(k)) or None,
            'lead_time':        row.get('lead_time'),
            'qty_expected':     qty,
            'rrp':              row.get('rrp'),
            'programma_status': row.get('programma_status'),
            'supplier':         row.get('supplier'),
            'url':              row.get('url'),
            'important_info':   row.get('important_info'),
            'notes':            row.get('notes'),
            'is_service':       is_service,
        })

    return lines, meta


# ───────────────────────────── routes ─────────────────────────────

@deliveries_bp.route('/deliveries')
def deliveries_page():
    return render_template('deliveries.html')


@deliveries_bp.route('/api/deliveries/parse', methods=['POST'])
def api_deliveries_parse():
    """Parse an uploaded Programma export and return the lines.

    Read-only preview: nothing is written to the database yet."""
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({'success': False, 'error': 'No file was uploaded.'}), 400

        raw = f.read()
        if not raw:
            return jsonify({'success': False, 'error': 'The uploaded file is empty.'}), 400

        lines, meta = parse_programma_xlsx(raw)
        deliverable = [l for l in lines if not l['is_service']]

        return jsonify({
            'success':      True,
            'filename':     f.filename,
            'meta':         meta,
            'counts': {
                'total':        len(lines),
                'deliverable':  len(deliverable),
                'service':      len(lines) - len(deliverable),
                'units':        sum(l['qty_expected'] for l in deliverable),
                'missing_sku':  sum(1 for l in deliverable if not l['sku']),
                'sections':     len(meta['sections']),
            },
            'lines':        lines,
        })
    except Exception as e:
        return jsonify({'success': False,
                        'error': f'{type(e).__name__}: {e}'}), 500
