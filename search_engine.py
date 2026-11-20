"""First-party YouTube search matching the search -> select -> extract workflow.

Based on the YouTubeProtocol search endpoint identified in the supplied Snaptube APK.
No third-party search service, download aggregator, or yt-dlp is used.
"""
import re
import time
import threading
from urllib.parse import quote_plus

import requests
from native_engine import BROWSER_HEADERS, ExtractError, SourceBlocked, fetch, parse_json_object

_SEARCH_CACHE={}
_SEARCH_LOCK=threading.RLock()
_COOLDOWN_UNTIL=0

def get_text(value):
    if isinstance(value,str):
        return value
    if isinstance(value,list):
        return ''.join(get_text(v) for v in value)
    if isinstance(value,dict):
        if isinstance(value.get('simpleText'),str):
            return value['simpleText']
        if isinstance(value.get('content'),str):
            return value['content']
        if 'runs' in value:
            return get_text(value['runs'])
        if 'text' in value:
            return get_text(value['text'])
        return ''
    return ''

def walk_videos(data):
    found={}
    def append_video(info):
        vid=info.get('videoId') or info.get('contentId')
        if not isinstance(vid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):
            return
        title=get_text(info.get('title')) or get_text(info.get('headline')) or 'Video sin título'
        if 'lockupMetadataViewModel' in info.get('metadata',{}):
            title=get_text(info['metadata']['lockupMetadataViewModel'].get('title')) or title
        author=(get_text(info.get('ownerText')) or get_text(info.get('longBylineText')) or
                get_text(info.get('shortBylineText')) or
                get_text(info.get('channelName')) or '')
        duration=(get_text(info.get('lengthText')) or
                  get_text(info.get('lengthSeconds')) or
                  get_text(info.get('videoDurationText')) or '')
        if not title or title=='Video sin título':
            return
        thumbnail=(info.get('thumbnail') or {})
        thumbs=thumbnail.get('thumbnails') or [] if isinstance(thumbnail,dict) else []
        # Fixed thumbnail CDN URL generated from a validated YouTube ID.
        result={
            'id':vid,
            'title':title[:160],
            'channel':author[:100],
            'duration':str(duration)[:35],
            'thumbnail':'https://i.ytimg.com/vi/'+vid+'/mqdefault.jpg',
        }
        if vid not in found:
            found[vid]=result
    def recursive(item,depth=0):
        if depth>35 or len(found)>=20:
            return
        if isinstance(item,list):
            for obj in item:
                recursive(obj,depth+1)
                if len(found)>=20:
                    break
        elif isinstance(item,dict):
            for key,value in item.items():
                if key in ('videoRenderer','compactVideoRenderer','gridVideoRenderer',
                           'reelItemRenderer','lockupViewModel','videoWithContextRenderer') and isinstance(value,dict):
                    append_video(value)
                if isinstance(value,(list,dict)):
                    recursive(value,depth+1)
                if len(found)>=20:
                    break
    recursive(data)
    return list(found.values())[:15]

def search_youtube(query):
    global _COOLDOWN_UNTIL
    query=(query or '').strip()
    if not (2<=len(query)<=120) or any(ord(c)<32 for c in query):
        raise ExtractError('Escribí entre 2 y 120 caracteres para buscar una canción o video.')
    now=time.time()
    with _SEARCH_LOCK:
        cached=_SEARCH_CACHE.get(query.lower())
        if cached and cached[0]>now:
            return cached[1]
        if now<_COOLDOWN_UNTIL:
            raise SourceBlocked(429)

    session=requests.Session()
    try:
        # The app's YouTubeProtocol search is first-party; fetch the public search HTML
        # to obtain the actual runtime Innertube context/key rather than bundling keys.
        page=fetch(session,'https://www.youtube.com/results?search_query='+quote_plus(query))
    except SourceBlocked as exc:
        if exc.status==429:
            with _SEARCH_LOCK:
                _COOLDOWN_UNTIL=time.time()+120
        raise

    data=None
    for marker in ('var ytInitialData =','window["ytInitialData"] =','ytInitialData ='):
        data=parse_json_object(page,marker)
        if isinstance(data,dict):
            break
    results=walk_videos(data or {})

    if not results:
        cfg=parse_json_object(page,'ytcfg.set(') or {}
        key=cfg.get('INNERTUBE_API_KEY')
        version=(cfg.get('INNERTUBE_CONTEXT') or {}).get('client',{}).get('clientVersion') or '2.20261120.00.00'
        if not key:
            m=re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"',page)
            if m:key=m.group(1)
        if key:
            try:
                r=session.post('https://www.youtube.com/youtubei/v1/search',params={'key':key},
                    headers={**BROWSER_HEADERS,'Content-Type':'application/json',
                             'Origin':'https://www.youtube.com','X-Youtube-Client-Name':'1',
                             'X-Youtube-Client-Version':version},
                    json={'query':query,'context':{'client':{
                        'clientName':'WEB','clientVersion':version,'hl':'es','gl':'AR'}}},
                    timeout=13)
                if r.status_code in (403,429):
                    if r.status_code==429:
                        with _SEARCH_LOCK:
                            _COOLDOWN_UNTIL=time.time()+120
                    raise SourceBlocked(r.status_code)
                r.raise_for_status()
                results=walk_videos(r.json())
            except (ValueError,requests.RequestException) as exc:
                if isinstance(exc,SourceBlocked):
                    raise
                raise ExtractError('La búsqueda de YouTube no está disponible desde este servidor.') from exc
    if not results:
        raise ExtractError('YouTube no entregó resultados de búsqueda. Probá con otras palabras o más tarde.')
    with _SEARCH_LOCK:
        _SEARCH_CACHE[query.lower()]=(time.time()+180,results)
        # Limit cache growth under public traffic
        if len(_SEARCH_CACHE)>80:
            for key in list(_SEARCH_CACHE)[:25]:
                _SEARCH_CACHE.pop(key,None)
    return results
