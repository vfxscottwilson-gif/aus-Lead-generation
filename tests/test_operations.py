"""Offline operational regression tests. No real providers or credentials used."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import operations as OP
import export_leads as EX
from localsheet import LocalSheet


def lead(**changes):
    row = {'Company Name': 'Example Dental', 'Website': 'https://example.test', 'City': 'Sydney',
        'Country': 'Australia', 'State': 'NSW', 'Full Address': '1 Example Street Sydney NSW 2000',
        'Source URL': 'https://example.test/contact', 'Decision Maker Full Name': 'Alex Example',
        'Name Source': 'https://example.test/team', 'Decision Maker Role': 'Owner', 'Role Evidence': 'Owner on team page',
        'Budget Evidence': 'Published premium services', 'Instagram': 'https://instagram.com/example',
        'Social Media Opportunity': 'Dated branded posts reviewed', 'Generic Email': 'info@example.test',
        'Email Source': 'https://example.test/contact', 'ICP Tier': 'A', 'Export Permission': 'Yes',
        'Export License Evidence': 'Official website reuse reviewed', 'Niche': 'Dental'}
    row.update(changes)
    return row


class OperationsTests(unittest.TestCase):
    def test_migration_idempotent_and_backup(self):
        sh = LocalSheet()
        ws = sh.add_worksheet('Dental')
        ws.update(values=[['Company Name', 'Decision Maker Name', 'First Name', 'My Notes'], ['Clinic', 'Alex Example', 'Alex', 'Keep']])
        OP.setup(sh)
        OP.setup(sh)
        self.assertEqual(ws.row_values(1)[1], 'Decision Maker Full Name')
        backups = [w for w in sh.worksheets() if w.title.startswith('Backup ')]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].row_values(1)[1], 'Decision Maker Name')
        self.assertEqual(ws.row_values(2), ['Clinic', 'Alex Example', 'Alex', 'Keep'])

    def test_country_evidence_not_domain_or_label(self):
        self.assertFalse(OP.australian({'Country': 'Australia', 'Website': 'https://clinic.com.au', 'City': 'Sydney'}))
        self.assertFalse(OP.australian(lead(Country='United States')))
        self.assertTrue(OP.australian(lead()))
        self.assertFalse(OP.australian(lead(**{'Source URL': ''})))

    def test_sync_dedup_multiple_people_and_manual_fields(self):
        sh = LocalSheet()
        OP.sync(sh, [lead(), lead(**{'Decision Maker Full Name': 'Jordan Example'})])
        self.assertEqual(len(OP.records(sh.worksheet('All Leads'))), 1)
        self.assertEqual(len(OP.records(sh.worksheet('Decision Makers'))), 2)
        ws = sh.worksheet('Outreach Leads')
        col = ws.row_values(1).index('Outreach Status') + 1
        ws.update(values=[['Contacted']], range_name=OP.rowcol_to_a1(2, col))
        OP.sync(sh, [lead()])
        self.assertEqual(OP.records(ws)[0]['Outreach Status'], 'Contacted')
        self.assertEqual(len(OP.records(ws)), 1)

    def test_adding_website_preserves_id(self):
        sh = LocalSheet()
        OP.sync(sh, [lead(Website='')])
        first = OP.records(sh.worksheet('All Leads'))[0]['Company ID']
        OP.sync(sh, [lead()])
        rows = OP.records(sh.worksheet('All Leads'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Company ID'], first)

    def test_suppression_excludes_qualified_and_export(self):
        sh = LocalSheet()
        OP.sync(sh, [lead()])
        ws = sh.worksheet('Outreach Leads')
        ws.update(values=[['Do Not Contact']], range_name=OP.rowcol_to_a1(2, ws.row_values(1).index('Outreach Status') + 1))
        result = OP.sync(sh, [lead()])
        self.assertEqual(result['Qualified Leads'], 0)
        self.assertEqual(EX.select_records(sh, 'companies'), [])

    def test_missing_evidence_in_research(self):
        sh = LocalSheet()
        result = OP.sync(sh, [lead(**{'Name Source': '', 'Budget Evidence': ''})])
        self.assertEqual(result['Qualified Leads'], 0)
        self.assertEqual(result['Research Queue Count'], 1)

    def test_niche_scan_ignores_reserved_and_backup(self):
        sh = LocalSheet()
        for title in ['Dental', 'All Leads', 'Backup Dental Names v1']:
            ws = sh.add_worksheet(title)
            row = lead()
            ws.update(values=[list(row), list(row.values())])
        self.assertEqual(len(OP.read_leads(sh)), 1)

    def test_dashboard_run_status_staleness_and_preservation(self):
        sh = LocalSheet()
        ws = sh.add_worksheet('Dashboard')
        ws.update(values=[['Existing'], ['=1+1']])
        OP.sync(sh, [lead()], run={'run_id': 'r1', 'status': 'success', 'added': 0, 'finished_at': '2020-01-01T00:00:00+00:00'})
        self.assertEqual(OP.records(sh.worksheet('Operational Runs'))[0]['Status'], 'no_qualifying_leads')
        OP.sync(sh, [], run={'run_id': 'r2', 'status': 'success', 'added': 1, 'finished_at': '2020-01-02T00:00:00+00:00'})
        result = OP.sync(sh, [], run={'run_id': 'r3', 'status': 'failed', 'error': 'Provider unavailable'})
        self.assertEqual(result['Data Freshness'], 'Stale')
        self.assertEqual(result['Current Workflow Status'], 'failed')
        self.assertEqual(ws.row_values(2)[0], '=1+1')

    def test_export_csv_xlsx_authorization_geo_and_injection(self):
        from unittest.mock import patch
        sh = LocalSheet()
        OP.sync(sh, [lead(**{'Company Name': '=DANGEROUS()', 'Phone Number': '+61212345678'}),
                     lead(Website='https://foreign.test', Country='Canada', **{'Company Name': 'Foreign Company'}),
                     lead(Website='https://unlicensed.test', **{'Company Name': 'Unlicensed', 'City': 'Melbourne', 'Export Permission': ''})])
        # Some sandbox runners place TEMP under their Git checkout. Mock only the
        # ancestor marker probe here; the separate test exercises the real safeguard.
        real_exists = Path.exists
        with tempfile.TemporaryDirectory() as folder, patch.object(
            Path, 'exists', autospec=True, side_effect=lambda path: False if path.name == '.git' else real_exists(path)
        ):
            path = Path(folder) / 'leads.csv'
            with self.assertRaises(PermissionError):
                EX.export(sh, path)
            self.assertEqual(EX.export(sh, path, authorized=True), 1)
            with path.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]['Company Name'], "'=DANGEROUS()")
            xlsx = Path(folder) / 'leads.xlsx'
            EX.export(sh, xlsx, authorized=True)
            with ZipFile(xlsx) as z:
                for filename in z.namelist():
                    ElementTree.fromstring(z.read(filename))
                xml = z.read('xl/worksheets/sheet1.xml').decode()
                self.assertNotIn('<f>', xml)
                self.assertIn('+61212345678', xml)
                self.assertNotIn('foreign.test', xml)
            with self.assertRaises(FileExistsError):
                EX.export(sh, path, authorized=True)

    def test_export_rejects_git_worktree_destination_without_writing(self):
        sh = LocalSheet()
        OP.sync(sh, [lead()])
        with tempfile.TemporaryDirectory() as folder:
            repository = Path(folder)
            (repository / '.git').mkdir()
            destination = repository / 'private' / 'leads.csv'
            with self.assertRaisesRegex(ValueError, 'outside a Git working tree'):
                EX.export(sh, destination, authorized=True)
            self.assertFalse(destination.exists())
            self.assertEqual(OP.records(sh.worksheet('Exports')), [])

    def test_multiple_raw_people_preserve_person_specific_mailboxes(self):
        sh = LocalSheet()
        row = lead(**{'Personal Email': 'alex@example.test', '_people': [
            ('Alex Example', 'Owner', 'https://example.test/team'),
            ('Jordan Example', 'Director', 'https://example.test/team')]})
        OP.sync(sh, [row])
        OP.sync(sh, [lead()])
        people = OP.records(sh.worksheet('Decision Makers'))
        self.assertEqual(len(people), 2)
        jordan = next(p for p in people if p['Decision Maker Full Name'] == 'Jordan Example')
        self.assertEqual(jordan['Personal Email'], '')

    def test_blacklist_prevents_exports_and_active_counts(self):
        sh = LocalSheet()
        ws = sh.add_worksheet('Blacklist')
        ws.update(values=[['Place Id / Email / Domain / Business name', 'Reason'], ['example.test', 'Opt out']])
        result = OP.sync(sh, [lead()])
        self.assertEqual(result['Qualified Leads'], 0)
        self.assertEqual(EX.select_records(sh, 'companies'), [])

    def test_failed_qualification_cannot_be_overridden_by_tier(self):
        sh = LocalSheet()
        result = OP.sync(sh, [lead(**{'Qualification Gates': 'Failed'})])
        self.assertEqual(result['Qualified Leads'], 0)

    def test_google_source_not_exported_even_with_generic_permission(self):
        sh = LocalSheet()
        OP.sync(sh, [lead(Source='Google Places')])
        self.assertEqual(EX.select_records(sh, 'companies'), [])

    def test_backup_failure_does_not_rename(self):
        from unittest.mock import patch
        sh = LocalSheet()
        ws = sh.add_worksheet('Dental')
        ws.update(values=[['Company Name', 'Decision Maker Name'], ['Clinic', 'Alex Example']])
        with patch.object(sh, 'add_worksheet', side_effect=RuntimeError('write denied')):
            with self.assertRaises(RuntimeError):
                OP.setup(sh)
        self.assertIn('Decision Maker Name', ws.row_values(1))

    def test_existing_all_leads_preserves_manual_cells_without_duplicate(self):
        sh = LocalSheet()
        ws = sh.add_worksheet('All Leads')
        row = lead(**{'Outreach Status': 'Contacted', 'Notes': 'Historical note'})
        ws.update(values=[list(row), list(row.values())])
        OP.sync(sh, [lead()])
        OP.sync(sh, [lead()])
        rows = OP.records(ws)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Notes'], 'Historical note')
        self.assertEqual(rows[0]['Outreach Status'], 'Contacted')
        self.assertTrue(rows[0]['Company ID'])

    def test_formula_all_leads_is_preserved_before_replacement(self):
        sh = LocalSheet()
        old = sh.add_worksheet('All Leads')
        old.update(values=[['Company Name'], ['=QUERY(Dental!A:Z)']])
        OP.setup(sh)
        self.assertEqual(sh.worksheet('Legacy All Leads').row_values(2)[0], '=QUERY(Dental!A:Z)')
        self.assertEqual(sh.worksheet('All Leads').row_values(1)[0], 'Company Name')
        OP.setup(sh)
        self.assertEqual(len([w for w in sh.worksheets() if w.title == 'All Leads']), 1)

    def test_gaps_do_not_shift_manual_rows_during_sync(self):
        sh = LocalSheet()
        ws = sh.add_worksheet('All Leads')
        row = lead(**{'Notes': 'Keep on row 3'})
        ws.update(values=[list(row), [''] * len(row), list(row.values())])
        OP.sync(sh, [lead()])
        self.assertEqual(ws.row_values(2), [])
        self.assertEqual(OP.records(ws)[0]['Notes'], 'Keep on row 3')
        self.assertEqual(OP.records(ws)[0]['_sheet_row'], 3)

    def test_contact_optout_prevents_company_export_without_resync(self):
        sh = LocalSheet()
        OP.sync(sh, [lead()])
        ws = sh.worksheet('Decision Makers')
        ws.update(values=[['Yes']], range_name=OP.rowcol_to_a1(2, ws.row_values(1).index('Opt Out') + 1))
        self.assertEqual(EX.select_records(sh, 'companies'), [])

    def test_many_niches_use_batched_reads_and_cached_setup(self):
        from unittest.mock import patch
        sh = LocalSheet()
        for number in range(45):
            ws = sh.add_worksheet('Niche ' + str(number))
            ws.update(values=[['Company Name', 'Website'], ['Business ' + str(number), '']])
        OP.setup(sh)
        with patch.object(sh, 'values_batch_get', wraps=sh.values_batch_get) as batch, patch.object(sh, 'worksheets', wraps=sh.worksheets) as metadata:
            OP.sync(sh, [lead()])
            self.assertEqual(metadata.call_count, 1)
            self.assertEqual(batch.call_count, 1)
        with patch.object(sh, 'values_batch_get', wraps=sh.values_batch_get) as batch, patch.object(sh, 'worksheets', wraps=sh.worksheets) as metadata:
            OP.sync(sh)
            self.assertEqual(metadata.call_count, 1)
            self.assertEqual(batch.call_count, 3)

    def test_retry_transient_errors_bounded_and_permanent_not_hidden(self):
        from unittest.mock import Mock, patch
        import gspread
        from localsheet import _FakeResp
        rate = gspread.exceptions.APIError(_FakeResp(429, 'quota'))
        permanent = gspread.exceptions.APIError(_FakeResp(403, 'access denied'))
        with patch.object(OP.time, 'sleep') as sleep:
            action = Mock(side_effect=[rate, TimeoutError(), 'ok'])
            self.assertEqual(OP.retry_io(action), 'ok')
            self.assertEqual(sleep.call_count, 2)
            action = Mock(side_effect=rate)
            with self.assertRaises(gspread.exceptions.APIError):
                OP.retry_io(action)
            self.assertEqual(action.call_count, 4)
            action = Mock(side_effect=permanent)
            with self.assertRaises(gspread.exceptions.APIError):
                OP.retry_io(action)
            self.assertEqual(action.call_count, 1)

    def test_retry_after_committed_write_does_not_duplicate_append(self):
        from unittest.mock import patch
        sh = LocalSheet()
        ws = sh.add_worksheet('Test')
        ws.update(values=[['Company ID', 'Value']])
        wrapped = OP._WorksheetIO(ws)
        original = ws.update
        calls = []
        def uncertain_write(*args, **kwargs):
            original(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError('response lost after commit')
        with patch.object(ws, 'update', side_effect=uncertain_write), patch.object(OP.time, 'sleep'):
            wrapped.append_rows([['id1', 'value']], value_input_option='RAW')
        self.assertEqual(len(OP.records(ws)), 1)

    def test_sync_read_failure_propagates(self):
        from unittest.mock import patch
        sh = LocalSheet()
        OP.setup(sh)
        with patch.object(sh, 'values_batch_get', side_effect=TimeoutError('unavailable')), patch.object(OP.time, 'sleep'):
            with self.assertRaises(TimeoutError):
                OP.sync(sh, [lead()])

    def test_saved_secondary_contacts_recover_after_lost_batch(self):
        import json
        sh = LocalSheet()
        ws = sh.add_worksheet('Dental')
        row = lead(**{'Contact Records JSON': json.dumps([
            ['Alex Example', 'Owner', 'https://example.test/team'],
            ['Jordan Example', 'Director', 'https://example.test/team']]),
            'Email Sources JSON': json.dumps({'team@example.test': 'https://example.test/contact'}),
            'Generic Email': '', 'Other Emails': 'team@example.test', 'Email Source': '',
            'Email Verification Details': json.dumps({'team@example.test': {'status': 'valid'}})})
        ws.update(values=[list(row), list(row.values())])
        OP.sync(sh)
        people = OP.records(sh.worksheet('Decision Makers'))
        self.assertEqual(len(people), 2)
        self.assertEqual(OP.records(sh.worksheet('All Leads'))[0]['Eligibility'], 'Ready For Review')
        self.assertIn('team@example.test', OP.records(sh.worksheet('All Leads'))[0]['Email Sources'])
        self.assertTrue(all(not person['Personal Email'] for person in people))
        self.assertEqual(len(EX.select_records(sh, 'companies')), 1)

    def test_other_email_needs_address_specific_source_and_usable_status(self):
        import json
        row = lead(**{'Generic Email': '', 'Other Emails': 'team@example.test', 'Email Source': '',
            'Email Sources': json.dumps({'unrelated@example.test': 'https://example.test/contact'})})
        self.assertFalse(OP.usable_emails(row))
        row['Email Sources'] = json.dumps({'team@example.test': 'https://example.test/contact'})
        self.assertEqual(OP.usable_emails(row), ['team@example.test'])
        row['Email Verification Details'] = json.dumps({'team@example.test': 'invalid'})
        self.assertFalse(OP.usable_emails(row))

    def test_company_downgrade_does_not_downgrade_other_company_contacts(self):
        sh = LocalSheet()
        first = lead(**{'Company Name': 'First Clinic', 'Website': 'https://first.test'})
        second = lead(**{'Company Name': 'Second Clinic', 'Website': 'https://second.test'})
        OP.sync(sh, [first, second])
        OP.sync(sh, [{**first, 'ICP Tier': 'C â€” Research Needed', 'Qualification Gates': 'Failed'}])
        people = {row['Company Name']: row for row in OP.records(sh.worksheet('Decision Makers'))}
        self.assertEqual(people['First Clinic']['Eligibility'], 'Research Needed')
        self.assertEqual(people['Second Clinic']['Eligibility'], 'Ready For Review')
        self.assertEqual(len(EX.select_records(sh, 'companies')), 1)

    def test_incomplete_c_never_exports_even_with_permission(self):
        sh = LocalSheet()
        OP.sync(sh, [lead(**{'ICP Tier': 'C â€” Research Needed', 'Qualification Gates': 'Failed'})])
        for kind in ('companies', 'decision-makers', 'outreach'):
            self.assertEqual(EX.select_records(sh, kind), [])

    def test_secondary_specialist_title_does_not_gain_owner_authority(self):
        sh = LocalSheet()
        OP.sync(sh, [lead(**{'_people': [('Jordan Example', 'Art Director', 'https://example.test/team')]})])
        person = next(r for r in OP.records(sh.worksheet('Decision Makers')) if r['Decision Maker Full Name'] == 'Jordan Example')
        self.assertEqual(person['Eligibility'], 'Research Needed')
        self.assertEqual(person['Role Evidence'], '')

    def test_canonical_saved_people_can_recover_without_niche_rows(self):
        import json
        sh = LocalSheet()
        OP.setup(sh)
        ws = sh.worksheet('All Leads')
        row = OP.normalize(lead(**{'Contact Records': json.dumps([
            ['Alex Example', 'Owner', 'https://example.test/team'],
            ['Jordan Example', 'Director', 'https://example.test/team']])}))
        ws.append_rows([[row.get(h, '') for h in ws.row_values(1)]])
        OP.sync(sh, [])
        self.assertEqual(len(OP.records(sh.worksheet('Decision Makers'))), 2)

    def test_csv_control_character_injection(self):
        for value in ['=X()', '  @SUM(A1)', '\t+12', '\r-X']:
            self.assertTrue(EX.safe_cell(value).startswith("'"))


if __name__ == '__main__':
    unittest.main()

