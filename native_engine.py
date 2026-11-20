"""Server-side adaptation of media detection seen in the supplied Snaptube APK.

Android WebView resource interception -> headless Chromium response interception.
Page/embedded player formats -> parsed directly, without yt-dlp or download APIs.
This independent port is not an exact binary port of proprietary Android code.
"""
import html
import ipaddress
import json
import re
import socket
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs
import requests
from vpn_linux import make_session, browser_proxy, ffmpeg_network_proxy
import threading

_BROWSE_LIMIT = threading.BoundedSemaphore(1)

MAX_BYTES = 220*1024*1024
UA = 'Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36'
BROWSER_HEADERS = {'User-Agent': UA, 'Accept-Language': 'es-AR,es;q=0.9,en;q=0.7'}

class ExtractError(ValueError):
    pass

class SourceBlocked(ExtractError):
    def __init__(self, status):
        self.status = status
        if status == 403:
            super().__init__('HTTP 403: YouTube o el sitio de origen rechazó el acceso del servidor Render.')
        else:
            super().__init__('HTTP 429: YouTube o el sitio de origen está limitando las solicitudes. Probá más tarde.')

def check_public_url(url):
    p = urlsplit(url)
    if not isinstance(url, str) or len(url)>4000 or p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ExtractError('Dirección multimedia inválida: se requiere HTTPS.')
    host = p.hostname.rstrip('.').lower()
    if host.endswith(('.local','.localhost','.internal')) or host in ('localhost','metadata.google.internal'):
        raise ExtractError('No se permiten direcciones de red internas.')
    try:
        addrs = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addrs or any(not ipaddress.ip_address(x[4][0]).is_global for x in addrs):
            raise ExtractError('No se permiten direcciones de red internas.')
    except socket.gaierror as ex:
        raise ExtractError('No se pudo localizar el servidor de video.') from ex
    return url

def fetch(session, url, stream=False, referer=None):
    for _ in range(5):
        check_public_url(url)
        headers = dict(BROWSER_HEADERS)
        if referer: headers['Referer'] = referer
        r = session.get(url, headers=headers, allow_redirects=False, stream=True, timeout=25)
        if r.status_code in (301,302,303,307,308):
            dest = r.headers.get('Location')
            r.close()
            if not dest: raise ExtractError('Redirección sin destino.')
            url = urljoin(url,dest)
            continue
        if r.status_code in (403,429):
            status = r.status_code
            r.close()
            raise SourceBlocked(status)
        r.raise_for_status()
        if stream: return r
        r.raw.decode_content = True
        raw = r.raw.read(7*1024*1024+1)
        encoding = r.encoding or 'utf-8'
        r.close()
        if len(raw)>7*1024*1024: raise ExtractError('La página es demasiado grande.')
        return raw.decode(encoding,errors='replace')
    raise ExtractError('Demasiadas redirecciones.')

def candidate(u,base):
    if not isinstance(u,str): return None
    u=html.unescape(u).strip().replace('\\/','/').replace('\\u0026','&').replace('\\u002F','/').replace('\\u003d','=')
    if u.startswith('//'): u='https:'+u
    if u.startswith('/'): u=urljoin(base,u)
    return u if u.startswith('https://') and len(u)<4000 else None

def is_media_url(u):
    p=urlsplit(u)
    text=p.path.lower()+'?'+p.query.lower()
    return any(p.path.lower().endswith(ext) for ext in ('.mp4','.m4v','.webm','.mov','.m3u8','.mpd','.mp3','.m4a')) or any(t in text for t in (
        '/videoplayback','/video/tos/','/playback/','/aweme/v1/play','/video_redirect','mime=video','mime=audio','/video/','/play/'))

def meta_extract(page,base):
    title=None; found=[]
    m=re.search(r'<title[^>]*>(.*?)</title>',page,re.I|re.S)
    if m: title=html.unescape(re.sub(r'<[^>]*>','',m.group(1))).strip()
    for tag in re.findall(r'<meta\s+[^>]*>',page,re.I|re.S):
        attrs=dict((k.lower(),html.unescape(v)) for k,v in re.findall(r'([\w:-]+)\s*=\s*["\x27]([^"\x27]*)["\x27]',tag))
        prop=(attrs.get('property') or attrs.get('name') or '').lower()
        val=attrs.get('content')
        if prop in ('og:title','twitter:title') and val: title=val
        if prop in ('og:video','og:video:url','og:video:secure_url','twitter:player:stream'):
            u=candidate(val,base)
            if u and (is_media_url(u) or urlsplit(u).path.lower().endswith(('.mp4','.webm','.m3u8','.m4v'))):
                found.append({'url':u,'score':90})
    for tag in re.findall(r'<(?:video|source|audio)\b[^>]*>',page,re.I):
        m=re.search(r'\b(?:src|data-src)\s*=\s*["\x27]([^"\x27]+)',tag,re.I)
        if m:
            u=candidate(m.group(1),base)
            if u:found.append({'url':u,'score':100})
    for m in re.finditer(r'<script\b[^>]*type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script>',page,re.I|re.S):
        try:
            data=json.loads(html.unescape(m.group(1)))
            def search(obj,key=''):
                if isinstance(obj,dict):
                    for k,v in obj.items(): search(v,k.lower())
                elif isinstance(obj,list):
                    for v in obj: search(v,key)
                elif isinstance(obj,str) and key in ('contenturl','videourl','video_url','playbackurl'):
                    u=candidate(obj,base)
                    if u:found.append({'url':u,'score':85})
            search(data)
        except (ValueError,TypeError): pass
    for m in re.finditer(r'["\x27]([\w-]*(?:url|addr|src|video|playback)[\w-]*)["\x27]\s*:\s*["\x27]((?:\\.|[^"\x27]){12,2500})["\x27]',page,re.I):
        key=m.group(1).lower()
        if any(x in key for x in ('image','avatar','thumb','cover','poster','icon','logo')): continue
        raw=m.group(2).replace('\\/','/').replace('\\u0026','&').replace('\\u002F','/')
        u=candidate(raw,base)
        if u and (is_media_url(u) or key in ('contenturl','playaddr','downloadaddr','playbackurl','videourl','video_url')):
            found.append({'url':u,'score':65})
    return title,found

def parse_json_object(text,marker):
    at=text.find(marker)
    if at<0: return None
    at=text.find('{',at+len(marker))
    if at<0:return None
    quote=False;escape=False;depth=0
    for end in range(at,min(len(text),at+2500000)):
        c=text[end]
        if escape:escape=False
        elif quote and c=='\\':escape=True
        elif c=='"':quote=not quote
        elif not quote:
            if c=='{':depth+=1
            elif c=='}':
                depth-=1
                if depth==0:
                    try:return json.loads(text[at:end+1])
                    except ValueError:return None
    return None

def youtube_id(original):
    """Only accept complete YouTube video identifiers; reject empty watch?v= links."""
    p = urlsplit(original)
    host = (p.hostname or '').lower()
    parts = [piece for piece in p.path.split('/') if piece]
    if host == 'youtu.be' or host.endswith('.youtu.be'):
        vid = parts[0] if parts else ''
    elif host == 'youtube.com' or host.endswith('.youtube.com'):
        vid = (parse_qs(p.query).get('v') or [''])[0]
        if not vid and len(parts) > 1 and parts[0] in ('shorts','live','embed','v'):
            vid = parts[1]
    else:
        return None
    return vid if re.fullmatch(r'[A-Za-z0-9_-]{11}', vid or '') else None


def youtube_extract(page, original, session):
    """Extract public player formats directly from YouTube's web/embed/Android player data."""
    vid = youtube_id(original)
    if not vid:
        raise ExtractError('El enlace de YouTube está incompleto. Usá Compartir y copiá la URL completa del video.')
    configs = []
    keys = []
    context = {}
    blocked = []
    has_sabr = False
    needs_signature = False
    pages = [page]
    try:
        embed = fetch(session, 'https://www.youtube.com/embed/'+vid+'?hl=es')
        pages.append(embed)
    except (ExtractError, requests.RequestException, ValueError):
        pass
    for text in pages:
        response = parse_json_object(text,'ytInitialPlayerResponse')
        if isinstance(response,dict):
            configs.append(response)
        for match in re.finditer(r'"playerResponse"\s*:\s*("(?:\\.|[^"\\])*")', text):
            try:
                value = json.loads(json.loads(match.group(1)))
                if isinstance(value,dict):
                    configs.append(value)
            except (ValueError, TypeError):
                continue
        cfg = parse_json_object(text,'ytcfg.set(') or {}
        if cfg.get('INNERTUBE_CONTEXT'):
            context = cfg['INNERTUBE_CONTEXT']
        key = cfg.get('INNERTUBE_API_KEY')
        if not key:
            m = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"',text)
            if m:
                key = m.group(1)
        if key and key not in keys:
            keys.append(key)
    client_configs = [
        ('ANDROID', '21.44.38'),
        ('WEB', str((context.get('client') or {}).get('clientVersion') or '2.20261120.00.00')),
        ('TVHTML5_SIMPLY_EMBEDDED_PLAYER', '2.0'),
    ]
    source_block_status=None
    if keys:
        for client, version in client_configs:
            try:
                client_context = {'clientName':client, 'clientVersion':version, 'hl':'es', 'gl':'AR'}
                if client == 'ANDROID':
                    client_context.update({'androidSdkVersion':33, 'osName':'Android','osVersion':'13'})
                if client == 'IOS':
                    client_context.update({'deviceMake':'Apple','deviceModel':'iPhone16,2','osName':'iOS','osVersion':'18.3'})
                body = {'videoId':vid,'context':{'client':client_context},'contentCheckOk':True,'racyCheckOk':True}
                if client == 'TVHTML5_SIMPLY_EMBEDDED_PLAYER':
                    body['context']['thirdParty']={'embedUrl':'https://www.youtube.com/'}
                response = session.post('https://www.youtube.com/youtubei/v1/player',
                    params={'key':keys[0]}, headers={**BROWSER_HEADERS,
                    'Content-Type':'application/json','Origin':'https://www.youtube.com',
                    'X-Youtube-Client-Name':{'ANDROID_VR':'28','TVHTML5_SIMPLY_EMBEDDED_PLAYER':'85','IOS':'5','ANDROID':'3','WEB':'1'}[client],
                    'X-Youtube-Client-Version':version},
                    json=body,timeout=8)
                if response.status_code in (403,429):
                    source_block_status=response.status_code
                    break
                if response.ok:
                    value = response.json()
                    if isinstance(value,dict):
                        configs.append(value)
            except (requests.RequestException,ValueError):
                continue
    formats=[]
    for config in configs:
        state = config.get('playabilityStatus') or {}
        if state.get('status') not in (None,'OK'):
            blocked.append(state.get('status','UNAVAILABLE'))
        streams = config.get('streamingData') or {}
        if streams.get('serverAbrStreamingUrl'):
            has_sabr=True
        for f in streams.get('formats',[])+streams.get('adaptiveFormats',[]):
            raw=f.get('url')
            if not raw:
                if f.get('signatureCipher') or f.get('cipher'):
                    needs_signature=True
                continue
            if not isinstance(raw,str) or not raw.startswith('https://'):
                continue
            mime=f.get('mimeType','')
            if 'video/' not in mime and 'audio/' not in mime:
                continue
            is_video='video/' in mime
            is_audio='audio/' in mime
            formats.append({'url':raw,'score':110 if 'mp4' in mime else 90,
                'height':f.get('height') or 0,'bitrate':f.get('bitrate') or 0,
                'has_video':is_video,
                'has_audio':is_audio or bool(f.get('audioQuality')) or (f.get('itag') in (18,22))})
        if streams.get('hlsManifestUrl'):
            formats.append({'url':streams['hlsManifestUrl'],'score':45,
                'has_video':True,'has_audio':True,'height':0})
        if streams.get('dashManifestUrl'):
            formats.append({'url':streams['dashManifestUrl'],'score':35,
                'has_video':True,'has_audio':True,'height':0})
    if not formats and source_block_status:
        raise SourceBlocked(source_block_status)
    if not formats and (has_sabr or needs_signature):
        raise ExtractError('YouTube exige un formato segmentado o una firma que este servidor no puede resolver para este video.')
    if not formats and blocked:
        status=blocked[0]
        if status in ('LOGIN_REQUIRED','AGE_CHECK_REQUIRED'):
            raise ExtractError('YouTube solicita iniciar sesión para este video. Solo se admiten videos públicos.')
        if status in ('UNPLAYABLE','ERROR'):
            raise ExtractError('YouTube no permite reproducir este video desde el servidor.')
    return formats

def browser_intercept(url):
    """Chromium response interception: only return actual media, never a fake player link."""
    results=[]
    title=None
    proxy_cfg = browser_proxy()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',
                args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu',
                      '--no-zygote','--autoplay-policy=no-user-gesture-required'],
                **({'proxy': proxy_cfg} if proxy_cfg else {}))
            try:
                context=browser.new_context(user_agent=UA,viewport={'width':420,'height':820},
                    locale='es-AR')
                page=context.new_page()
                def on_response(resp):
                    try:
                        u=resp.url
                        mime=resp.headers.get('content-type','').lower()
                        is_media=('video/' in mime or 'audio/' in mime or
                            'mpegurl' in mime or 'dash+xml' in mime)
                        if ('html' in mime or 'json' in mime or 'yt-ump' in mime):
                            return
                        if u.startswith('https://') and (is_media or is_media_url(u)):
                            if ('/videoplayback' not in u or 'range=' not in u):
                                results.append({'url':u,'score':86})
                    except Exception:
                        pass
                page.on('response',on_response)
                page.goto(url,wait_until='domcontentloaded',timeout=20000)
                page.wait_for_timeout(2500)
                try:
                    title=page.title()
                    urls=page.locator('video, audio, source').evaluate_all(
                        'nodes => nodes.map(n => n.currentSrc || n.src).filter(Boolean)')
                    for u in urls:
                        if u.startswith('https://'):
                            results.append({'url':u,'score':92})
                except Exception:
                    pass
                if not results:
                    try:
                        page.locator('video').first.click(timeout=1600)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass
                context.close()
            finally:
                browser.close()
    except Exception:
        pass
    return title,results

def choose(found,mode,quality):
    dedup={}
    for c in found:
        u=c.get('url')
        if not u or not u.startswith('https://'):continue
        c={'has_audio':True,'has_video':True,'height':0,'bitrate':0,'score':50,**c}
        if u not in dedup or c['score']>dedup[u]['score']:dedup[u]=c
    found=list(dedup.values())
    if not found:raise ExtractError('No se encontró un enlace multimedia directo. Puede requerir sesión, firma o token.')
    if mode=='audio':
        options=[c for c in found if c['has_audio']] or found
        options.sort(key=lambda c:(not c['has_video'],c['score'],c['bitrate']),reverse=True)
    else:
        options=[c for c in found if c['has_video']]
        if not options:raise ExtractError('No se detectó video.')
        maximum=int(quality)
        options.sort(key=lambda c:(1 if c['height'] and c['height']<=maximum else (0 if c['height']==0 else -1),
            min(c['height'],maximum),c['score'],c['has_audio']),reverse=True)
    result=options[0].copy()
    if mode=='video' and not result['has_audio']:
        audio=[c for c in found if c['has_audio'] and not c['has_video']]
        if audio:result['audio_url']=max(audio,key=lambda c:c['bitrate'])['url']
    return result

def extract_media(url,mode,quality):
    session=make_session()
    found=[]
    title=''
    upstream_error=None
    host=(urlsplit(url).hostname or '').lower()
    is_youtube=(host=='youtu.be' or host.endswith('.youtu.be') or
                host=='youtube.com' or host.endswith('.youtube.com'))
    if is_youtube and not youtube_id(url):
        raise ExtractError('El enlace de YouTube está incompleto. Copiá el enlace completo desde Compartir.')
    try:
        page=fetch(session,url)
        title,meta=meta_extract(page,url)
        found.extend(meta)
        if is_youtube:
            try:
                found.extend(youtube_extract(page,url,session))
            except ExtractError as exc:
                upstream_error=str(exc)
    except SourceBlocked as exc:
        upstream_error=str(exc)
        if exc.status == 429:
            raise
    except (requests.RequestException,ExtractError,ValueError) as exc:
        upstream_error=str(exc)
    if not found:
        browser_title, media = browser_intercept(url)
        found.extend(media)
        title=title or browser_title
    if not found and upstream_error:
        raise ExtractError(upstream_error)
    if not found and is_youtube:
        raise ExtractError('YouTube no entregó un archivo directo. Puede requerir firmas, sesión o un token de reproducción.')
    result=choose(found,mode,quality)
    result['title']=(title or 'Video sin título')[:100]
    result['referer']=url
    result['_cookies']=session.cookies.copy()
    return result

def stream_download(session,url,path,referer,progress):
    with fetch(session,url,stream=True,referer=referer) as resp:
        length=int(resp.headers.get('Content-Length') or 0)
        if length>MAX_BYTES:raise ExtractError('El archivo supera los 220 MB.')
        written=0
        with open(path,'wb') as file:
            for chunk in resp.iter_content(128*1024):
                if not chunk:continue
                file.write(chunk);written+=len(chunk)
                if written>MAX_BYTES:raise ExtractError('El archivo supera los 220 MB.')
                progress('downloading',min(93,int(90*written/length)) if length else 12)
    if not written:raise ExtractError('El archivo está vacío.')

def ffmpeg(args):
    try:
        proc=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y',*args],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=175)
    except subprocess.TimeoutExpired as ex:
        raise ExtractError('La conversión excedió el tiempo disponible.') from ex
    if proc.returncode:
        raise ExtractError('No se pudo convertir este formato: '+proc.stderr.decode('utf8','replace')[-180:])

def download_media(info,folder,mode,quality,progress):
    url=check_public_url(info['url'])
    ref=info.get('referer') or url
    session=make_session()
    jar=info.get('_cookies')
    if jar:
        session.cookies.update(jar)
    folder=Path(folder)
    out=folder/('resultado.mp3' if mode=='audio' else 'resultado.mp4')
    if urlsplit(url).path.lower().endswith(('.m3u8','.mpd')):
        headers=f'Referer: {ref}\r\nUser-Agent: {UA}\r\n'
        proxy_url=ffmpeg_network_proxy()
        inp=([ '-http_proxy', proxy_url ] if proxy_url else [])+['-headers',headers,'-i',url]
    else:
        path=folder/'source.media'
        stream_download(session,url,path,ref,progress)
        inp=['-i',str(path)]
    progress('processing',96)
    if mode=='audio':
        args=inp+['-vn','-map','0:a:0','-c:a','libmp3lame','-b:a',quality+'k',str(out)]
    elif info.get('audio_url'):
        path=folder/'source_audio.media'
        stream_download(session,check_public_url(info['audio_url']),path,ref,progress)
        args=inp+['-i',str(path),'-map','0:v:0','-map','1:a:0',
            '-c:v','copy','-c:a','aac','-movflags','+faststart',str(out)]
    else:
        args=inp+['-map','0:v:0','-map','0:a?','-c:v','copy','-c:a','aac',
            '-movflags','+faststart',str(out)]
    ffmpeg(args)
    if not out.is_file() or out.stat().st_size<1024:raise ExtractError('El archivo no se pudo generar.')
    if out.stat().st_size>MAX_BYTES:raise ExtractError('El resultado supera los 220 MB.')
    progress('processing',99)
    return out
