"""End-to-end offline contracts for the Australia-only orchestration."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import defaults as D
import leadbot as LB
import localsheet as LS
import operations as OP
import research
import yt


class CoreUpgrade(unittest.TestCase):
    def setUp(self):
        LB._CACHE.update(ws={}, vals={}, sh=None)
        LB._HDRS.clear()
        isolated = {k: v for k, v in os.environ.items() if k in ('TEMP', 'TMP', 'SYSTEMROOT', 'WINDIR', 'USERPROFILE')}
        isolated.update(SOURCES='input', USE_MX_CHECK='false')
        self.env = mock.patch.dict(os.environ, isolated, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def sheet(self):
        sh = LS.LocalSheet()
        with mock.patch.object(LB, 'open_sheet', return_value=sh):
            LB.main(local=True, setup_only=True)
        return sh

    def add_candidate(self, sh, complete=True):
        source = 'https://harperdental.com.au/team'
        row = {'Business Name': 'Harper Dental', 'Website': 'https://harperdental.com.au',
               'Niche': 'Dentists', 'Country': 'Australia', 'State': 'NSW', 'City': 'Sydney',
               'Source URL': 'https://harperdental.com.au/contact',
               'Instagram': 'https://www.instagram.com/harperdental'}
        if complete:
            row.update({'Decision Maker Full Name': 'Janet Harper', 'Decision Maker Role': 'Owner',
                'Name Source': source, 'Personal Email': 'janet@harperdental.com.au', 'Email Source': source,
                'Social Opportunity': 'Add short educational videos',
                'Social Evidence': 'Reviewed 10 recent posts; all are static service photographs',
                'Social Source': row['Instagram'], 'Commercial Evidence JSON': json.dumps([
                    {'kind': 'published_pricing', 'evidence': 'Published treatment pricing', 'source': source},
                    {'kind': 'booking', 'evidence': 'Online appointment system observed', 'source': source}])})
        ws = sh.worksheet('Leads Input')
        ws.append_row([row.get(h, '') for h in ws.row_values(1)])

    def run_input(self, sh):
        info = {'website': 'https://harperdental.com.au', 'domain': 'harperdental.com.au',
                'emails': [], 'email_source': '', 'socials': {}, 'notes': [], 'people': [],
                'phones': [], 'signals': [], 'text': '', 'email_sources': {}, 'email_associations': {}}
        with mock.patch.object(LB, 'open_sheet', return_value=sh), \
             mock.patch.object(LB, 'read_site', return_value=info), \
             mock.patch.object(LB.O, 'verify_email', return_value='valid'), \
             mock.patch.object(LB.O, 'jina_read', return_value=None):
            LB.main(local=True)

    def test_qualified_names_and_email_survive_full_pipeline_and_rerun(self):
        sh = self.sheet()
        self.add_candidate(sh)
        self.run_input(sh)
        rows = OP.records(sh.worksheet('All Leads'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Decision Maker Full Name'], 'Janet Harper')
        self.assertEqual(rows[0]['Personal Email'], 'janet@harperdental.com.au')
        self.assertTrue(rows[0]['ICP Tier'].startswith('A'), rows[0])
        self.assertEqual(len(OP.records(sh.worksheet('Decision Makers'))), 1)
        ow = sh.worksheet('Outreach Leads')
        hdr = ow.row_values(1)
        ow.update(values=[['Replied']], range_name=LB.rowcol_to_a1(2, hdr.index('Outreach Status') + 1))
        self.run_input(sh)
        self.assertEqual(len(OP.records(sh.worksheet('All Leads'))), 1)
        self.assertEqual(OP.records(ow)[0]['Outreach Status'], 'Replied')

    def test_missing_name_and_email_go_to_research(self):
        sh = self.sheet()
        self.add_candidate(sh, complete=False)
        self.run_input(sh)
        rows = OP.records(sh.worksheet('Research Queue'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['Decision Maker Full Name'], '')
        self.assertTrue(rows[0]['ICP Tier'].startswith('C'))
        run = OP.records(sh.worksheet('Operational Runs'))[-1]
        self.assertEqual(run['Status'], 'no_qualifying_leads')

    def test_foreign_and_unlocated_query_results_never_inherit_australia(self):
        a = LB.parse_address({'addressComponents': []}, {'country': 'Australia', 'cc': 'AU'})
        self.assertEqual(a['cc'], '')
        places = LB.google_places_from([{'title': 'Unknown', 'website': 'https://unknown.com.au'}],
                                      {'country': 'Australia', 'state': 'NSW', 'city': 'Sydney'}, 'Serper')
        self.assertEqual(LB.parse_address(places[0])['cc'], '')
        self.assertFalse(yt.country_ok('US', 'Australia in description', LB.Settings({})))
        self.assertFalse(yt.country_ok('', 'Australian business', LB.Settings({'YT_ALLOW_UNKNOWN_COUNTRY': 'Yes'})))

    def test_single_sheet_and_missing_core_failure(self):
        with mock.patch.dict(os.environ, {'SHEET_ID_AU_ONLY': 'old', 'SHEET_ID': 'new'}):
            self.assertEqual(LB.sheet_id_for(), 'new')
        with self.assertRaises(ValueError):
            LB.sheet_id_for('US')
        with self.assertRaises(SystemExit):
            LB.open_sheet()

    def test_only_australian_seeds_and_all_territories(self):
        self.assertTrue(all(l[0] == 'Australia' for l in D.LOCATIONS))
        self.assertEqual({l[1] for l in D.LOCATIONS}, {'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT', 'NT'})
        self.assertTrue(all(len(n) == len(D.NICHES_H) for n in D.NICHES))
        self.assertTrue(all(n[1] != 'YouTube' or ' AU ' in n[0] for n in D.NICHES))

    def test_operational_and_backup_tabs_not_rescanned_as_niches(self):
        sh = LS.LocalSheet()
        for title in ('All Leads', 'Outreach Leads', 'Decision Makers', 'Backup Dentists', 'Dentists'):
            sh.add_worksheet(title).update(values=[['Company Name', 'Place Id'], ['X', 'one']])
        self.assertEqual(set(LB.scan_lead_tabs(sh)), {'Dentists'})

    def test_required_finalization_failure_releases_lock_and_fails(self):
        sh = self.sheet()
        real = OP.sync
        def fail_dashboard(*args, **kwargs):
            if kwargs.get('run'):
                raise RuntimeError('simulated dashboard failure')
            return real(*args, **kwargs)
        with mock.patch.object(LB, 'open_sheet', return_value=sh), mock.patch.object(OP, 'sync', side_effect=fail_dashboard):
            with self.assertRaisesRegex(RuntimeError, 'finalization failed'):
                LB.main(local=True)
        self.assertEqual(dict((r[:2] for r in sh.worksheet('Meta').get_all_values()[1:] if len(r)>=2))['lock'], '')

    def test_research_json_requires_structured_sourced_evidence(self):
        with self.assertRaises(ValueError):
            research.attach({}, {'Commercial Evidence JSON': 'not JSON'})
        row = research.attach({}, {'Commercial Evidence JSON': '[{"kind":"budget","evidence":"guess"}]'})
        self.assertEqual(row['_commercial_evidence'], [])

    def test_dry_run_never_opens_google(self):
        with mock.patch.object(LB.gspread, 'service_account_from_dict', side_effect=AssertionError('live access')):
            LB.cli(['--dry', '--setup'])


if __name__ == '__main__':
    unittest.main()
