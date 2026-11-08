"""Recorded sending events never imply this collector sent email."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import operations as OP
from localsheet import LocalSheet

class EmailDashboardTests(unittest.TestCase):
    def test_empty_log_and_duplicate_or_unverified_events(self):
        sh = LocalSheet()
        summary = OP.sync(sh, [])
        self.assertEqual(summary['Total Emails Sent'], 0)
        ws = sh.worksheet('Email Events')
        header = ws.row_values(1)
        events = [
            {'Event ID': '1', 'Message ID': 'm1', 'Event Type': 'Sent', 'Sent At UTC': '2026-01-01T10:00:00+00:00', 'Source': 'Manual sending log'},
            {'Event ID': '2', 'Message ID': 'm1', 'Event Type': 'Sent', 'Sent At UTC': '2026-01-01T10:00:00+00:00', 'Source': 'Duplicate import'},
            {'Event ID': '3', 'Event Type': 'Draft', 'Sent At UTC': '2026-01-02T10:00:00+00:00', 'Source': 'Draft tool'},
            {'Event ID': '4', 'Event Type': 'Sent', 'Sent At UTC': '2026-01-03T10:00:00+00:00'},
        ]
        ws.append_rows([[e.get(h, '') for h in header] for e in events])
        summary = OP.sync(sh, [])
        self.assertEqual(summary['Total Emails Sent'], 1)
        self.assertEqual(summary['Last Email Sent UTC'], events[0]['Sent At UTC'])
        self.assertIn('not connected', summary['Email Tracking Basis'])

if __name__ == '__main__':
    unittest.main()
