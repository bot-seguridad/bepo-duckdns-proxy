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

from native_engine import extract_media, download_media, youtube_id
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
    if (host == 'youtube.com' or host.endswith('.youtube.com') or
            host == 'youtu.be' or host.endswith('.youtu.be')):
        if not youtube_id(raw.strip()):
            raise ValueError('El enlace de YouTube está incompleto. Abrí el video, tocá Compartir y copiá el enlace completo.')
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
    def progress(status, percent):
        update(job_id, status=status, percent=percent)
    try:
        update(job_id, status='extracting', percent=2)
        info = extract_media(url, mode, quality)
        progress('downloading', 4)
        output = download_media(info, folder, mode, quality, progress)
        title = info.get('title') or 'Video sin título'
        name = re.sub(r'[^\w .()\-]+', '_', title, flags=re.UNICODE).strip(' ._') or 'descarga'
        extension = '.mp3' if mode == 'audio' else '.mp4'
        update(job_id, status='ready', percent=100, title=title, path=str(output), filename=name + extension)
    except Exception as exc:
        app.logger.warning('Download failed for platform=%s error_type=%s',
                           urlsplit(url).hostname, type(exc).__name__)
        message = str(exc)
        if 'HTTP 429' in message or '429 Client Error' in message:
            message = 'La plataforma está limitando las solicitudes desde este servidor (429). Esperá antes de intentar de nuevo.'
        elif 'HTTP 403' in message or '403 Client Error' in message:
            message = 'La plataforma rechazó la conexión desde Render (403). Cambiar de calidad no soluciona esta restricción.'
        elif 'HTTPError' in type(exc).__name__:
            message = 'El servidor de origen rechazó la descarga. Probá con otro contenido público.'
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
    return jsonify(ok=True, engine='webview-request-interception', ffmpeg=bool(shutil.which('ffmpeg')), chromium=os.path.isfile('/usr/bin/chromium'))


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
