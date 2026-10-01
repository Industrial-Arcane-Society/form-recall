import unittest
from unittest import mock
import urllib.error

from ai_modeler.agent import online


BING_HTML = '''<ol><li class="b_algo"><h2><a href="https://example.org/item">Useful title</a></h2><p>Useful snippet</p></li></ol>'''
DDG_HTML = '''<div class="result"><a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fref">Fallback result</a><a class="result__snippet">Fallback summary</a></div>'''


class OnlineSearchTests(unittest.TestCase):
    def setUp(self):
        self.cache = online._CACHE.copy()
        online._CACHE.clear()

    def tearDown(self):
        online._CACHE.clear()
        online._CACHE.update(self.cache)

    def test_bing_search_preserves_legacy_row_shape_and_diagnostics(self):
        with mock.patch.object(online, '_fetch', return_value=BING_HTML), \
             mock.patch.object(online, '_save_cache'):
            result = online.search_detailed('useful', use_cache=False)
        self.assertEqual(result['diagnostics']['status'], 'ok')
        self.assertEqual(result['diagnostics']['engine'], 'bing')
        self.assertEqual(result['results'][0], {
            'title': 'Useful title', 'snippet': 'Useful snippet',
            'url': 'https://example.org/item'})

    def test_duckduckgo_fallback_is_used_after_bing_parser_change(self):
        with mock.patch.object(online, '_fetch', side_effect=[
                '<html>new bing layout</html>', DDG_HTML]), \
             mock.patch.object(online, '_save_cache'):
            result = online.search_detailed('fallback', use_cache=False)
        self.assertEqual(result['diagnostics']['engine'], 'duckduckgo')
        self.assertTrue(result['diagnostics']['fallback_used'])
        self.assertEqual(result['results'][0]['url'], 'https://example.org/ref')

    def test_timeout_and_http_error_are_distinguished(self):
        with mock.patch.object(online, '_fetch', side_effect=TimeoutError()), \
             mock.patch.object(online, '_save_cache'):
            result = online.search_detailed('timeout', use_cache=False)
        self.assertEqual(result['diagnostics']['status'], 'timeout')
        with mock.patch.object(online, '_fetch', side_effect=urllib.error.HTTPError(
                'https://example.org', 503, 'unavailable', {}, None)), \
             mock.patch.object(online, '_save_cache'):
            result = online.search_detailed('http failure', use_cache=False)
        self.assertEqual(result['diagnostics']['status'], 'http_error')
        self.assertEqual(result['diagnostics']['attempts'][0]['http_status'], 503)

    def test_legacy_search_returns_list(self):
        with mock.patch.object(online, 'search_detailed', return_value={'results': [{'url': 'x'}]}):
            self.assertEqual(online.search('q'), [{'url': 'x'}])

    def test_open_page_reports_body_hash_and_fetch_failure(self):
        with mock.patch.object(online, '_fetch', return_value='<html><p>hello</p></html>'):
            result = online.fetch_page_text_detailed('https://example.org')
        self.assertEqual(result['text'], 'hello')
        self.assertEqual(result['diagnostics']['status'], 'ok')
        self.assertEqual(len(result['diagnostics']['content_sha256']), 64)
        with mock.patch.object(online, '_fetch', side_effect=TimeoutError()):
            result = online.fetch_page_text_detailed('https://example.org')
        self.assertEqual(result['diagnostics']['status'], 'timeout')


if __name__ == '__main__':
    unittest.main()
