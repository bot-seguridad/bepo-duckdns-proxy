import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, validate_link
from native_engine import (
    ExtractError, youtube_id, youtube_extract, meta_extract, choose,
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
        self.assertIn('hideFile()', ui)

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

    def test_empty_source_no_fake_link(self):
        with self.assertRaises(ExtractError):
            choose([], 'video', '720')


if __name__ == '__main__':
    unittest.main()
