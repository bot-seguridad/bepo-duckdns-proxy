import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, validate_link, SEARCH_SELECTIONS, SEARCH_RATES
from search_engine import walk_videos, get_text
from vpn_linux import make_session, browser_proxy, public_proxy_status, outbound_endpoint, ProxySetupError
from native_engine import (
    ExtractError, SourceBlocked, fetch, youtube_id, youtube_extract, meta_extract, choose,
)


class OrbitaSmokeTests(unittest.TestCase):
    def test_home_and_health(self):
        client = app.test_client()
        self.assertEqual(client.get('/').status_code, 200)
        response = client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ffmpeg'])

    def test_download_is_hidden_until_ready(self):
        content = Path('static/style.css').read_text(encoding='utf-8')
        self.assertIn('[hidden] { display: none !important; }', content)
        ui = Path('static/app.js').read_text(encoding='utf-8')
        self.assertIn('clearDownload()', ui)

    def test_youtube_full_link_required(self):
        self.assertEqual(youtube_id('https://youtube.com/watch?v=BaW_jenozKc'), 'BaW_jenozKc')
        self.assertEqual(youtube_id('https://youtu.be/BaW_jenozKc'), 'BaW_jenozKc')
        self.assertEqual(youtube_id('https://www.youtube.com/shorts/BaW_jenozKc'), 'BaW_jenozKc')
        self.assertIsNone(youtube_id('https://youtube.com/watch?v='))
        with self.assertRaises(ValueError):
            validate_link('https://youtube.com/watch?v=')
        self.assertEqual(app.test_client().post('/api/download', json={
            'url': 'https://youtube.com/watch?v=', 'mode': 'video', 'quality': '720'
        }).status_code, 400)

    def test_meta_parse_real_video(self):
        title, items = meta_extract(
            '<title>Prueba</title><video src="https://media.example.org/video.mp4"></video>',
            'https://example.org/watch')
        self.assertEqual(title, 'Prueba')
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['url'].endswith('.mp4'))

    def test_youtube_player_direct_format(self):
        page = ('<script>var ytInitialPlayerResponse = '
                '{"streamingData":{"formats":[{"url":'
                '"https://r.googlevideo.com/videoplayback?itag=18",'
                '"mimeType":"video/mp4","height":360,"itag":18}]}};</script>')
        with patch('native_engine.fetch', side_effect=ExtractError('test offline')):
            formats = youtube_extract(page, 'https://youtube.com/watch?v=BaW_jenozKc', __import__('requests').Session())
        self.assertTrue(formats)
        self.assertTrue(formats[0]['has_video'])
        self.assertTrue(formats[0]['has_audio'])
        self.assertEqual(choose(formats, 'video', '720')['height'], 360)

    def test_vpn_diagnostic_route_and_zip_export(self):
        response = app.test_client().get('/vpn-diagnostico')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Diagn\xc3\xb3stico de conexi\xc3\xb3n VPN', response.data)
        self.assertIn(b'zipFile(entries)', response.data)
        self.assertIn(b'api64.ipify.org', response.data)
        self.assertNotIn(b'Vpn-Super-Unlimited-v2', response.data)

    def test_linux_proxy_adapter_defaults_to_safe_off(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'ORBITA_OUTBOUND_PROXY':''}):
            self.assertIsNone(outbound_endpoint())
            self.assertIsNone(browser_proxy())
            self.assertEqual(public_proxy_status(),'unconfigured')
            self.assertFalse(make_session().proxies)

    def test_linux_proxy_routes_search_and_download_transport(self):
        from unittest.mock import patch
        value='socks5://example.net:1080'
        with patch.dict('os.environ', {'ORBITA_OUTBOUND_PROXY':value}):
            session=make_session()
            self.assertEqual(session.proxies['https'], 'socks5h://example.net:1080')
            self.assertEqual(session.proxies['http'], 'socks5h://example.net:1080')
            self.assertEqual(browser_proxy()['server'], 'socks5://example.net:1080')
            self.assertEqual(public_proxy_status(),'configured')

    def test_linux_proxy_never_leaks_credentials(self):
        from unittest.mock import patch
        value='socks5h://user:secret@example.net:1080'
        with patch.dict('os.environ', {'ORBITA_OUTBOUND_PROXY':value}):
            self.assertEqual(public_proxy_status(),'configured')
            self.assertIn('user:secret@',make_session().proxies['https'])
            self.assertEqual(browser_proxy()['server'], 'socks5://example.net:1080')
            self.assertNotIn('secret', repr(public_proxy_status()))
        with patch.dict('os.environ', {'ORBITA_OUTBOUND_PROXY':'not-a-proxy'}):
            with self.assertRaises(ProxySetupError):
                outbound_endpoint()
            self.assertEqual(public_proxy_status(),'invalid')

    def test_search_parser_finds_youtube_videos(self):
        sample = {
            'contents': {
                'twoColumnSearchResultsRenderer': {
                    'primaryContents': {
                        'sectionListRenderer': {
                            'contents': [{
                                'itemSectionRenderer': {
                                    'contents': [{'videoRenderer': {
                                        'videoId': 'BaW_jenozKc',
                                        'title': {'runs': [{'text': 'Video de prueba'}]},
                                        'ownerText': {'runs': [{'text': 'Canal de prueba'}]},
                                        'lengthText': {'simpleText': '2:37'}
                                    }}]
                                }
                            }]
                        }
                    }
                }
            }
        }
        results = walk_videos(sample)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], 'BaW_jenozKc')
        self.assertEqual(results[0]['channel'], 'Canal de prueba')
        self.assertEqual(results[0]['duration'], '2:37')

    def test_search_then_download_from_selected_result(self):
        result = {
            'id':'BaW_jenozKc','title':'Video de prueba',
            'channel':'Canal de prueba', 'duration':'2:37',
            'thumbnail':'https://i.ytimg.com/vi/BaW_jenozKc/mqdefault.jpg',
        }
        client = app.test_client()
        with patch('app.search_youtube', return_value=[result]):
            response = client.get('/api/search?q=cancion+de+prueba')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['results'][0]['id'],'BaW_jenozKc')
        with patch('app.EXECUTOR.submit') as executor:
            response = client.post('/api/download', json={
                'video_id':'BaW_jenozKc','mode':'video','quality':'720'
            })
        self.assertEqual(response.status_code, 202)
        args = executor.call_args.args
        self.assertEqual(args[2], 'https://www.youtube.com/watch?v=BaW_jenozKc')

    def test_link_not_required_on_frontend(self):
        html = Path('templates/index.html').read_text(encoding='utf-8')
        js = Path('static/app.js').read_text(encoding='utf-8')
        self.assertIn('id="search-query"',html)
        self.assertIn('id="search-results"',html)
        self.assertNotIn('id="source-url"',html)
        self.assertIn('/api/search',js)
        self.assertIn('video_id:selected.id',js)

    def test_source_403_and_429_no_retries(self):
        from unittest.mock import Mock
        for status in (403, 429):
            response = Mock(status_code=status)
            session = Mock()
            session.get.return_value = response
            with patch('native_engine.check_public_url', return_value='https://youtube.com/watch?v=BaW_jenozKc'):
                with self.assertRaises(SourceBlocked) as err:
                    fetch(session, 'https://youtube.com/watch?v=BaW_jenozKc')
            self.assertEqual(err.exception.status, status)
            self.assertEqual(session.get.call_count, 1)
            response.close.assert_called_once()

    def test_youtube_stops_at_429(self):
        from unittest.mock import Mock
        page = '<script>ytcfg.set({"INNERTUBE_API_KEY":"TEST_KEY"});</script>'
        session = Mock()
        session.post.return_value = Mock(status_code=429, ok=False)
        with patch('native_engine.fetch', side_effect=ExtractError('offline')):
            with self.assertRaises(SourceBlocked) as err:
                youtube_extract(page, 'https://youtube.com/watch?v=BaW_jenozKc', session)
        self.assertEqual(err.exception.status, 429)
        self.assertEqual(session.post.call_count, 1)

    def test_empty_source_no_fake_link(self):
        with self.assertRaises(ExtractError):
            choose([], 'video', '720')


if __name__ == '__main__':
    unittest.main()
