"""Explicit, private, Australia-only export. This module never sends or publishes data."""
from __future__ import annotations
import argparse
import csv
import re
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

import operations as OP

FIELDS = list(dict.fromkeys(['Company ID', 'Contact ID'] + [h for h in OP.OUTREACH if h not in {'Company ID', 'Contact ID', 'Place Id', 'Map Url'}]))


def safe_cell(value):
    value = OP.text(value)
    # Prefix hazardous CSV input even when obscured by whitespace/control characters.
    stripped = value.lstrip(' \t\r\n\ufeff')
    if stripped.startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def exportable(row):
    # Google-sourced discovery records require a separately sourced/licensed record before export.
    if re.search(r'google|places|maps', OP.text(row.get('Source')), re.I):
        return False
    return (OP.australian(row) and not OP.suppressed(row) and row.get('Eligibility') == 'Ready For Review' and not OP.reason(row)
            and OP.norm(row.get('Export Permission')) == 'yes' and bool(OP.text(row.get('Export License Evidence'))))


def select_records(sh, kind):
    """Read-only selection; no sync, migration or network enrichment on export."""
    sh = OP.sheet_io(sh)
    sh.prime(['All Leads', 'Decision Makers', 'Outreach Leads', 'Suppression', 'Blacklist'])
    tabs = {'companies': 'All Leads', 'decision-makers': 'Decision Makers', 'outreach': 'Outreach Leads'}
    if kind not in tabs:
        raise ValueError('Unsupported export kind')
    suppression = OP.suppression_rows(sh)
    outreach = OP.records(sh.worksheet('Outreach Leads'))
    suppression += [{'Company ID': r.get('Company ID', '')} for r in outreach if OP.suppressed(r)]
    result, seen = [], set()
    for raw in OP.records(sh.worksheet(tabs[kind])):
        row = OP.normalize(raw)
        if not exportable(row) or OP.suppressed(row, suppression):
            continue
        if kind == 'outreach' and (OP.reason(row) or row.get('Eligibility') != 'Ready For Review'):
            continue
        if kind == 'decision-makers' and (not row.get('Decision Maker Full Name') or not row.get('Name Source')):
            continue
        key = OP.contact_id(row) if kind == 'decision-makers' else OP.company_id(row)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def write_xlsx(path, rows):
    # Every cell is inline text: Excel never interprets user values as formulas or strips phone +/zeros.
    def cell(col, number, value):
        clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', OP.text(value))
        return f'<c r="{OP.rowcol_to_a1(number, col)}" t="inlineStr"><is><t xml:space="preserve">{escape(clean)}</t></is></c>'
    data = [FIELDS] + [[r.get(k, '') for k in FIELDS] for r in rows]
    sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>'
    sheet += ''.join(f'<row r="{n}">' + ''.join(cell(c, n, v) for c, v in enumerate(row, 1)) + '</row>' for n, row in enumerate(data, 1))
    sheet += '</sheetData><autoFilter ref="A1:' + OP.rowcol_to_a1(len(data), len(FIELDS)) + '"/></worksheet>'
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Australian Leads" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', sheet)


def export(sh, path, kind='companies', *, authorized=False):
    if not authorized:
        raise PermissionError('Explicit export authorization is required')
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in {'.csv', '.xlsx'}:
        raise ValueError('Output must end in .csv or .xlsx')
    # Refuse all Git working trees, including a destination nested in one.
    if any((parent / '.git').exists() for parent in (path.parent, *path.parent.parents)):
        raise ValueError('Choose a private destination outside a Git working tree')
    if path.exists():
        raise FileExistsError('Export destination already exists; choose a new file')
    sh = OP.sheet_io(sh)
    rows = select_records(sh, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == '.csv':
        with path.open('x', encoding='utf-8-sig', newline='') as fh:
            writer = csv.writer(fh)
            writer.writerow(FIELDS)
            writer.writerows([[safe_cell(r.get(k, '')) for k in FIELDS] for r in rows])
    else:
        write_xlsx(path, rows)
    # Audit includes a filename only, never contact data or sensitive absolute paths.
    stamp = OP.now()
    ws = OP.ensure(sh, 'Exports', OP.SCHEMAS['Exports'])
    OP.upsert(ws, [{'Export ID': OP.stable_id('ex_', stamp + str(path)), 'Created At': stamp,
        'Kind': kind, 'Format': path.suffix[1:], 'Rows': len(rows), 'Destination': path.name,
        'Status': 'Created privately'}], 'Export ID')
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['companies', 'decision-makers', 'outreach'], required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--authorize-export', action='store_true', required=True)
    parser.add_argument('--local-dir', help='Read a LocalSheet CSV folder instead of Google Sheets')
    args = parser.parse_args(argv)
    if args.local_dir:
        from localsheet import LocalSheet
        sh = LocalSheet(args.local_dir)
    else:
        from leadbot import open_sheet
        sh = open_sheet()
    count = export(sh, args.output, args.kind, authorized=args.authorize_export)
    if args.local_dir:
        sh.save()
    print(f'Exported {count} authorized Australian records privately.')


if __name__ == '__main__':
    main()
