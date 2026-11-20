import os
import re
import time
import uuid
import shutil
import tempfile
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file, abort

app = Flask(__name__)
app.json.ensure_ascii = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024

WORKDIR = Path(tempfile.gettempdir()) / 'orbita-media-web'
WORKDIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_BYTES = 220 * 1024 * 1024
JOB_LIFETIME = 20 * 60
QUEUE_LIMIT = 10
ALLOWED_DOMAINS = (
    'youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com',
    'facebook.com', 'fb.watch', 'twitter.com', 'x.com',
    'vimeo.com', 'dailymotion.com', 'dai.ly', 'soundcloud.com',
    'reddit.com', 'redd.it', 'pinterest.com', 'pin.it',
    'twitch.tv', 'rumble.com', 'bilibili.com',
)
JOBS = {}
RATES = defaultdict(deque)
LOCK = threading.RLock()
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='media')


def validate_link(raw):
    if not isinstance(raw, str) or not (12 <= len(raw) <= 1800):
        raise ValueError('Pegá un enlace válido de una plataforma compatible.')
    try:
        parts = urlsplit(raw.strip())
        host = (parts.hostname or '').lower().rstrip('.')
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError
        if not any(host == d or host.endswith('.' + d) for d in ALLOWED_DOMAINS):
            raise ValueError
        if any(ord(c) < 32 for c in raw) or '\\' in raw:
            raise ValueError
    except ValueError:
        raise ValueError('Usá un enlace HTTPS de YouTube, TikTok, Instagram u otra plataforma admitida.')
    return raw.strip()


def cleanup():
    now = time.time()
    with LOCK:
        for key, record in list(JOBS.items()):
            if record['status'] not in ('ready', 'error'):
                continue
            if now - record['updated'] > JOB_LIFETIME:
                JOBS.pop(key, None)
                shutil.rmtree(WORKDIR / key, ignore_errors=True)
        for address, times in list(RATES.items()):
            while times and now - times[0] > 600:
                times.popleft()
            if not times:
                RATES.pop(address, None)


def update(job_id, **fields):
    with LOCK:
        record = JOBS.get(job_id)
        if record:
            record.update(fields)
            record['updated'] = time.time()


def process(job_id, url, mode, quality):
    folder = WORKDIR / job_id
    folder.mkdir(parents=True, exist_ok=True)

    def hook(data):
        if data.get('status') == 'downloading':
            done = data.get('downloaded_bytes') or 0
            if done > MAX_FILE_BYTES:
                raise yt_dlp.utils.DownloadError('El archivo supera el límite de 220 MB.')
            size = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
            pct = min(98, round(done / size * 100)) if size else 0
            update(job_id, status='downloading', percent=pct)
        elif data.get('status') == 'finished':
            update(job_id, status='processing', percent=99)

    opts = {
        'outtmpl': str(folder / '%(id)s.%(ext)s'),
        'noplaylist': True,
        'ignoreerrors': False,
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True,
        'overwrites': False,
        'cachedir': False,
        'socket_timeout': 25,
        'retries': 2,
        'fragment_retries': 2,
        'concurrent_fragment_downloads': 3,
        'max_filesize': MAX_FILE_BYTES,
        'progress_hooks': [hook],
        'windowsfilenames': True,
    }
    if mode == 'audio':
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality,
            }],
        })
    else:
        height = int(quality)
        opts.update({
            'format': (
                'bv*[height<={0}][ext=mp4]+ba[ext=m4a]/'
                'b[height<={0}][ext=mp4]/'
                'bv*[height<={0}]+ba/'
                'b[height<={0}]/best'
            ).format(height),
            'merge_output_format': 'mp4',
            'postprocessors': [{'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mp4'}],
        })
    try:
        update(job_id, status='extracting')
        with yt_dlp.YoutubeDL(opts) as downloader:
            info = downloader.extract_info(url, download=True)
            if not info:
                raise ValueError('La plataforma no entregó un archivo descargable.')
            title = (info.get('title') or 'video')[:110]
        wanted = '.mp3' if mode == 'audio' else '.mp4'
        outputs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == wanted]
        if not outputs:
            raise ValueError('No se pudo obtener el formato solicitado.')
        output = max(outputs, key=lambda p: p.stat().st_size)
        if output.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('El archivo supera el límite de 220 MB.')
        name = re.sub(r'[^\w .()\-]+', '_', title, flags=re.UNICODE).strip(' ._') or 'descarga'
        update(job_id, status='ready', percent=100, title=title, path=str(output), filename=name + wanted)
    except Exception as exc:
        message = str(exc)
        if len(message) > 180:
            message = message[:177] + '...'
        update(job_id, status='error', error=message or 'Error al procesar el enlace.')
        shutil.rmtree(folder, ignore_errors=True)


@app.after_request
def headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['X-Frame-Options'] = 'DENY'
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/')
def home():
    cleanup()
    return render_template('index.html')


@app.route('/api/health')
def health():
    return jsonify(ok=True, engine='yt-dlp', ffmpeg=bool(shutil.which('ffmpeg')))


@app.route('/api/download', methods=['POST'])
def start():
    cleanup()
    data = request.get_json(silent=True) or {}
    try:
        url = validate_link(data.get('url'))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    mode = data.get('mode')
    quality = str(data.get('quality'))
    if (mode == 'audio' and quality not in ('128', '192', '320')) or (mode == 'video' and quality not in ('360', '720', '1080')):
        return jsonify(error='Formato o calidad inválidos.'), 400
    address = (request.headers.get('X-Forwarded-For', '').split(',')[0] or request.remote_addr or 'unknown').strip()
    with LOCK:
        recent = RATES[address]
        now = time.time()
        while recent and now - recent[0] > 600:
            recent.popleft()
        if len(recent) >= 6:
            return jsonify(error='Límite temporal: máximo 6 descargas cada 10 minutos.'), 429
        pending = sum(1 for record in JOBS.values() if record['status'] not in ('ready', 'error'))
        if pending >= QUEUE_LIMIT:
            return jsonify(error='Hay demasiadas descargas en curso. Probá nuevamente en un rato.'), 503
        recent.append(now)
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            'status': 'queued', 'percent': 0, 'created': now, 'updated': now,
            'error': None, 'title': None, 'filename': None,
        }
    EXECUTOR.submit(process, job_id, url, mode, quality)
    return jsonify(job=job_id), 202


@app.route('/api/job/<job_id>')
def job_status(job_id):
    cleanup()
    with LOCK:
        record = JOBS.get(job_id)
        if not record:
            return jsonify(error='Esta descarga ya no está disponible.'), 404
        return jsonify({k: record.get(k) for k in ('status', 'percent', 'title', 'error', 'filename')})


@app.route('/api/file/<job_id>')
def file_download(job_id):
    cleanup()
    with LOCK:
        record = JOBS.get(job_id)
        if not record or record['status'] != 'ready':
            abort(404)
        filepath, filename = record['path'], record['filename']
    if not os.path.isfile(filepath):
        abort(404)
    return send_file(filepath, as_attachment=True, download_name=filename, conditional=True)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)), threaded=True)
