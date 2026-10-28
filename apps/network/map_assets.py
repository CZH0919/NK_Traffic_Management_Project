"""Read-only access to the one installed regional PMTiles archive."""
import re
from pathlib import Path
from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_safe
from apps.core.permissions import require_capability


def archive_path():
    return Path(settings.MAP_ARCHIVE)


def archive_available():
    try:
        with archive_path().open('rb') as stream:
            return stream.read(8) == b'PMTiles\x03'
    except OSError:
        return False


def _chunks(path, start, length):
    with path.open('rb') as stream:
        stream.seek(start)
        while length:
            chunk = stream.read(min(65536, length))
            if not chunk:
                return
            length -= len(chunk)
            yield chunk


@require_capability('analyze_data')
@require_safe
def archive(request):
    path = archive_path()
    try:
        stat = path.stat()
    except OSError:
        return HttpResponse('参考底图尚未安装，可以使用基础地图。', status=404)
    size = stat.st_size
    etag = f'"{stat.st_mtime_ns:x}-{size:x}"'
    headers = {'Accept-Ranges': 'bytes', 'ETag': etag,
               'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff'}
    if request.headers.get('If-None-Match') == etag and not request.headers.get('Range'):
        return HttpResponse(status=304, headers=headers)
    start, end, status = 0, size - 1, 200
    requested = request.headers.get('Range')
    if requested and request.headers.get('If-Range', etag) == etag:
        match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', requested)
        try:
            if not match or not any(match.groups()):
                raise ValueError
            a, b = match.groups()
            if a:
                start, end = int(a), min(int(b), size - 1) if b else size - 1
            else:
                suffix = int(b)
                if suffix <= 0:
                    raise ValueError
                start = max(0, size - suffix)
            if start < 0 or start >= size or end < start:
                raise ValueError
        except ValueError:
            return HttpResponse(status=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
        status = 206
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    length = max(0, end - start + 1)
    headers['Content-Length'] = str(length)
    if request.method == 'HEAD':
        return HttpResponse(status=status, content_type='application/vnd.pmtiles', headers=headers)
    return StreamingHttpResponse(_chunks(path, start, length), status=status,
                                 content_type='application/vnd.pmtiles', headers=headers)
