import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import enrich
import qualification as q
import insight

URL = 'https://brightdental.com.au/team'


def candidate():
    return {'Company Name': 'Bright Dental', 'Country Code': 'AU', 'State': 'NSW',
            'Full Address': '12 King St, Sydney NSW 2000, Australia', 'Map Url': 'https://maps.google.com/?cid=123',
            'Niche': 'Dentists', 'Website': 'https://brightdental.com.au', '_domain': 'brightdental.com.au',
            '_people': [('Jane Smith', 'Owner', URL)], 'Generic Email': 'info@brightdental.com.au',
            '_email_sources': {'info@brightdental.com.au': URL}, 'Instagram': 'https://instagram.com/brightdental',
            '_commercial_evidence': [{'kind': k, 'evidence': v, 'source': URL} for k, v in
                                     [('pricing', 'Published implant pricing'), ('demand', 'Current client projects'), ('booking', 'Online appointments')]],
            '_social_observations': {'source': 'https://instagram.com/brightdental', 'posts30': 2,
                                     'window_days': 30, 'complete_window': True}}


class QualificationTests(unittest.TestCase):
    def test_qualified_and_weights(self):
        out = q.evaluate(candidate())
        self.assertEqual(out['ICP Tier'], 'A — Priority')
        self.assertEqual(out['ICP Score'], 93)
        self.assertIn('not a confirmed budget', out['Budget Fit'])

    def test_mandatory_gates_override_score(self):
        for key in ('_people', '_commercial_evidence', '_social_observations', 'Instagram', '_email_sources', 'Full Address'):
            lead = candidate(); lead.pop(key)
            self.assertEqual(q.evaluate(lead)['ICP Tier'], 'C — Research Needed', key)

    def test_overseas_rejected(self):
        lead = candidate(); lead['Country Code'] = 'US'
        self.assertEqual(q.evaluate(lead)['ICP Tier'], 'Rejected')

    def test_domain_not_location_evidence(self):
        self.assertEqual(q.evaluate({'Website': 'https://abc.com.au'})['ICP Tier'], 'C — Research Needed')

    def test_no_invented_name_or_capacity(self):
        out = q.evaluate({'Personal Email': 'jane.smith@brightdental.com.au', 'Niche': 'Dentists'})
        self.assertEqual(out['Decision Maker Full Name'], '')
        self.assertEqual(out['Personal Email'], '')
        self.assertEqual(out['Budget Evidence'], '')

    def test_no_personal_from_substring(self):
        p, g, other = enrich.classify_emails(['joann@brightdental.com.au', 'info@brightdental.com.au'], 'brightdental.com.au', 'Ann Doe')
        self.assertEqual((p, g), ('', 'info@brightdental.com.au'))
        self.assertEqual(other, ['joann@brightdental.com.au'])

    def test_association_and_normalization(self):
        p, g, other = enrich.classify_emails([' JANE@brightdental.com.au ', 'jane@brightdental.com.au', 'broken'], '', 'Jane Smith',
                                            associations={'jane@brightdental.com.au': {'name': 'Jane Smith', 'source': URL}})
        self.assertEqual((p, g, other), ('jane@brightdental.com.au', '', []))

    def test_generic_never_personal(self):
        p, g, _ = enrich.classify_emails(['info@brightdental.com.au'], '', 'Jane Smith',
                                        associations={'info@brightdental.com.au': {'name': 'Jane Smith', 'source': URL}})
        self.assertEqual((p, g), ('', 'info@brightdental.com.au'))

    def test_json_person_provenance(self):
        data = {'@type': 'Person', 'name': 'Jane Smith', 'jobTitle': 'Owner', 'email': 'jane@brightdental.com.au'}
        html = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
        self.assertEqual(enrich.email_associations_in(html, URL)['jane@brightdental.com.au'], {'name': 'Jane Smith', 'source': URL})

    def test_team_page_proximity_not_association(self):
        self.assertEqual(enrich.email_associations_in('<p>Jane Smith - Owner</p><a href="mailto:joe@brightdental.com.au">Email</a>', URL), {})

    def test_manager_not_authority(self):
        lead = candidate(); lead['_people'] = [('Jane Smith', 'Marketing Manager', URL)]
        self.assertEqual(q.evaluate(lead)['Qualification Gates'], 'Failed')

    def test_old_role_not_current(self):
        self.assertEqual(enrich.people_in('', 'Jane Smith was the owner.'), [])
        lead = candidate(); lead['_people'] = [('Jane Smith', 'Former Owner', URL)]
        self.assertEqual(q.evaluate(lead)['Qualification Gates'], 'Failed')

    def test_ai_cannot_substitute_role(self):
        self.assertEqual(insight.verify_person('Jane Smith', 'Owner', 'Jane Smith - Practice Manager'), ('Jane Smith', 'Practice Manager'))

    def test_partial_social_window_not_low_posting(self):
        lead = candidate(); lead['_social_observations']['complete_window'] = False
        self.assertEqual(q.evaluate(lead)['Social Media Opportunity'], 'Research Needed')

    def test_invalid_email_blocks_qualification(self):
        lead = candidate(); lead['Email Verified'] = 'invalid'
        self.assertEqual(q.evaluate(lead)['Qualification Gates'], 'Failed')

    def test_settings_normalized_and_input_immutable(self):
        lead = candidate(); original = copy.deepcopy(lead)
        out = q.evaluate(lead, {'ICP_WEIGHT_AUTHORITY': 300})
        self.assertLessEqual(out['ICP Score'], 100)
        self.assertEqual(lead, original)

    def test_malformed_email_rejected(self):
        self.assertEqual(enrich.classify_emails(['jane..smith@brightdental.com.au', 'jane@-brightdental.com.au'], ''), ('', '', []))

    def test_former_role_in_text_not_current(self):
        self.assertEqual(enrich.people_in('', 'Former owner Jane Smith'), [])

    def test_unknown_measurements_never_imply_gap(self):
        import score
        self.assertEqual(score.social_gap({}, None)[0], 0)
        self.assertEqual(score.yt_gap(None)[0], 0)

    def test_revenue_not_synthesized(self):
        import quality
        band, basis = quality.revenue_band({'velocity': 20}, {'ticket': 300}, 8, .8, None)
        self.assertEqual(band, 'Unverified')
        self.assertNotIn('/mo', basis)

    def test_duplicate_and_suppression(self):
        for flag in ('_duplicate', '_suppressed', '_major_corporate', '_closed'):
            lead = candidate(); lead[flag] = True
            self.assertEqual(q.evaluate(lead)['ICP Tier'], 'Rejected')



class ProviderHardeningTests(unittest.TestCase):
    def test_hunter_preserves_public_sources(self):
        import optional as opt
        from unittest.mock import patch, Mock
        rows = [{'value': 'jane@brightdental.com.au', 'confidence': 95, 'first_name': 'Jane', 'last_name': 'Smith',
                 'position': 'Owner', 'sources': [{'uri': URL}]},
                {'value': 'joe@brightdental.com.au', 'confidence': 95, 'first_name': 'Joe', 'last_name': 'Brown', 'position': 'Owner'}]
        response = Mock(); response.json.return_value = {'data': {'emails': rows}}
        with patch.object(opt, 'key', return_value='test'), patch.object(opt, '_call', return_value=response):
            found = opt.hunter_find('brightdental.com.au')
        self.assertEqual(found['email_sources'], {'jane@brightdental.com.au': URL})
        self.assertEqual(found['people'], [('Jane Smith', 'Owner', URL, 'jane@brightdental.com.au')])
        self.assertNotIn('joe@brightdental.com.au', found['email_associations'])
        self.assertIn('joe@brightdental.com.au', found['emails'])

    def test_mx_timeout_null_and_implicit(self):
        import optional as opt
        import dns.resolver
        import dns.exception
        from unittest.mock import patch, Mock
        cases = [([dns.exception.Timeout()], None),
                 ([[Mock(exchange='.')]], 'invalid'),
                 ([dns.resolver.NoAnswer(), dns.exception.Timeout(), dns.resolver.NoAnswer()], None),
                 ([dns.resolver.NoAnswer(), dns.resolver.NoAnswer(), [Mock()]], 'mx-ok'),
                 ([dns.resolver.NXDOMAIN()], 'invalid')]
        for responses, expected in cases:
            opt._MX.clear()
            with patch('dns.resolver.resolve', side_effect=responses):
                self.assertEqual(opt.mx_check('jane@brightdental.com.au'), expected)

    def test_domain_check_does_not_skip_mailbox_verifier(self):
        import optional as opt
        from unittest.mock import patch
        with patch.object(opt, 'disify_check', return_value='mx-ok'), patch.object(opt, 'reacher_check', return_value='valid') as verifier:
            self.assertEqual(opt.verify_email('jane@brightdental.com.au'), 'valid')
            verifier.assert_called_once()

    def test_robots_errors_fail_closed(self):
        import requests
        from unittest.mock import patch, Mock
        for result in (Mock(status_code=401), Mock(status_code=403), Mock(status_code=503), requests.Timeout()):
            enrich._robots.clear()
            with patch.object(enrich.requests, 'get', side_effect=[result]):
                self.assertFalse(enrich.allowed(URL))
        enrich._robots.clear()
        with patch.object(enrich.requests, 'get', return_value=Mock(status_code=404)):
            self.assertTrue(enrich.allowed(URL))
        enrich._robots.clear()

    def test_platform_pages_never_fetched(self):
        from unittest.mock import patch
        import optional as opt
        for url in ('https://instagram.com/someone', 'https://www.linkedin.com/in/jane', 'https://m.facebook.com/company'):
            with patch.object(enrich.requests, 'get') as get, patch.object(opt, '_call') as call:
                self.assertIsNone(enrich.fetch(url))
                self.assertIsNone(opt.jina_read(url))
                self.assertIsNone(opt.zenrows_fetch(url))
                get.assert_not_called(); call.assert_not_called()

    def test_redirect_cannot_fetch_platform(self):
        from unittest.mock import patch, Mock
        enrich._robots.clear()
        response = Mock(status_code=302, headers={'location': 'https://instagram.com/someone'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(enrich.requests, 'get', side_effect=[Mock(status_code=404), response]) as get:
            self.assertIsNone(enrich.fetch(URL))
            self.assertEqual(get.call_count, 2)
        enrich._robots.clear()


class IntegrationEvidenceTests(unittest.TestCase):
    def test_specialist_director_is_not_assumed_owner(self):
        for title in ('Art Director', 'Clinical Director', 'Principal Software Engineer', 'Marketing Director'):
            lead = candidate(); lead['_people'] = [('Jane Smith', title, URL)]
            self.assertEqual(q.evaluate(lead)['Qualification Gates'], 'Failed', title)
            self.assertEqual(q.evaluate(lead)['Decision Maker Full Name'], 'Jane Smith')

    def test_sample_metadata_supports_video_observation_only(self):
        import optional as opt
        posts = [('2025-01-01T00:00:00Z', 3, 1, '', False)] * 5
        obs = opt._stats(100, posts)
        self.assertEqual(obs['sample_size'], 5)
        self.assertFalse(obs['complete_window'])
        lead = candidate(); lead['_social_observations'] = dict(obs, source=lead['Instagram'])
        result = q.evaluate(lead)
        self.assertIn('5 sampled posts', result['Social Media Opportunity'])
        self.assertNotIn('posting frequency', result['Social Media Opportunity'])

    def test_unknown_media_type_is_not_no_video(self):
        import optional as opt
        obs = opt._stats(100, [('2025-01-01T00:00:00Z', 3, 1, '', None)] * 5)
        self.assertIsNone(obs['reels_share'])

    def test_future_post_not_counted_in_recent_activity(self):
        import optional as opt
        obs = opt._stats(100, [('2099-01-01T00:00:00Z', 3, 1, '', True)])
        self.assertEqual(obs['posts30'], 0)


if __name__ == '__main__':
    unittest.main()
