"""Download only the two original datasets used in the first EDA session."""
from pathlib import Path
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
import shutil
import urllib.request
import urllib.error
import io
import struct
import time
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
LOGS = ROOT / 'logs'
YOUTUBE_URL = 'https://archive.ics.uci.edu/static/public/380/youtube%2Bspam%2Bcollection.zip'
RDD_REPO = 'https://github.com/sekilab/RoadDamageDetector'

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attributes):
        if tag == 'a':
            self.links.extend(value for key, value in attributes if key == 'href' and value)

def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def fetch(url, filename):
    path = RAW / filename
    if path.exists():
        print(f'Using existing {filename}: {path.stat().st_size:,} bytes', flush=True)
    else:
        temp = path.with_suffix(path.suffix + '.part')
        print(f'Downloading {filename}', flush=True)
        with urllib.request.urlopen(url, timeout=90) as response, temp.open('wb') as output:
            total = 0
            last = 0
            while chunk := response.read(4 * 1024 * 1024):
                output.write(chunk)
                total += len(chunk)
                if total - last >= 50 * 1024 * 1024:
                    print(f'{filename}: {total / 1024**2:.0f} MiB', flush=True)
                    last = total
        temp.replace(path)
    return {'url': url, 'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
            'sha256': sha256(path), 'checked_at': datetime.now(timezone.utc).isoformat()}

class RemoteZipIndex(io.RawIOBase):
    """Read only the small ZIP directory with validated HTTP range requests."""
    def __init__(self, url, size):
        self.url, self.size, self.position = url, size, 0
    def seek(self, offset, whence=0):
        self.position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        return self.position
    def tell(self):
        return self.position
    def seekable(self):
        return True
    def range_response(self, start, end):
        url = self.url + '?range_request=' + str(time.time_ns())
        request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
        response = urllib.request.urlopen(request, timeout=120)
        if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
            response.close()
            raise RuntimeError('Server did not honor the bounded HTTP range request')
        return response
    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if size <= 0:
            return b''
        if size > 16 * 1024**2:
            raise RuntimeError('Unexpectedly large ZIP directory read')
        with self.range_response(self.position, self.position + size - 1) as response:
            result = response.read()
        self.position += len(result)
        return result

def fetch_country_from_figshare(metadata, filename):
    """The official Figshare archive contains seven country ZIPs; fetch just one."""
    file = next(item for item in metadata['files'] if item['name'].endswith('.zip'))
    remote = RemoteZipIndex(file['download_url'], file['size'])
    with zipfile.ZipFile(remote) as archive:
        country_name = filename.removeprefix('RDD2022_')
        entries = [entry for entry in archive.infolist() if entry.filename.endswith('/' + country_name)]
        if len(entries) != 1:
            raise RuntimeError(f'Expected one country archive, found {[x.filename for x in archive.infolist()]}')
        entry = entries[0]
        remote.seek(entry.header_offset)
        header = remote.read(30)
        assert header[:4] == b'PK\x03\x04'
        name_length, extra_length = struct.unpack_from('<HH', header, 26)
        start = entry.header_offset + 30 + name_length + extra_length
        print(f'Fetching only {entry.filename}: {entry.compress_size / 1024**2:.1f} MiB from official Figshare ZIP', flush=True)
        path = RAW / filename
        temp = path.with_suffix('.zip.part')
        decompressor = zlib.decompressobj(-15) if entry.compress_type == zipfile.ZIP_DEFLATED else None
        assert entry.compress_type in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED)
        crc, written, received, last = 0, 0, 0, 0
        with remote.range_response(start, start + entry.compress_size - 1) as response, temp.open('wb') as output:
            while chunk := response.read(4 * 1024**2):
                received += len(chunk)
                data = decompressor.decompress(chunk) if decompressor else chunk
                output.write(data)
                crc = zlib.crc32(data, crc)
                written += len(data)
                if received - last >= 50 * 1024**2:
                    print(f'Figshare country archive: {received / 1024**2:.0f} MiB', flush=True)
                    last = received
            if decompressor:
                data = decompressor.flush()
                output.write(data)
                crc = zlib.crc32(data, crc)
                written += len(data)
        assert received == entry.compress_size and written == entry.file_size
        assert (crc & 0xffffffff) == entry.CRC, 'Outer archive CRC check failed'
        with zipfile.ZipFile(temp) as inner:
            assert inner.testzip() is None, 'Country archive integrity check failed'
        temp.replace(path)
    return {'url': file['download_url'], 'member': entry.filename, 'method': 'validated HTTP range extraction from official Figshare archive',
            'path': str(path.relative_to(ROOT)), 'bytes': written, 'sha256': sha256(path),
            'checked_at': datetime.now(timezone.utc).isoformat()}

def main():
    RAW.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    parser = Links()
    parser.feed(urllib.request.urlopen(RDD_REPO, timeout=60).read().decode())
    candidates = [url for url in parser.links if url.endswith('/RDD2022_United_States.zip')]
    if not candidates:
        raise RuntimeError('The author repository no longer links the United States archive')
    rdd_url = candidates[0]
    print('Observed official archive URL:', rdd_url, flush=True)
    previous = ROOT / 'outputs' / 'coursework_part1' / 'youtube_spam.zip'
    if previous.exists() and not (RAW / 'youtube_spam.zip').exists():
        shutil.copy2(previous, RAW / 'youtube_spam.zip')
    with urllib.request.urlopen('https://api.figshare.com/v2/articles/21431547', timeout=60) as response:
        metadata = json.load(response)
    (RAW / 'rdd2022_figshare_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    downloads = [fetch(YOUTUBE_URL, 'youtube_spam.zip')]
    try:
        downloads.append(fetch(rdd_url, 'RDD2022_United_States.zip'))
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
        print('Author S3 link returns 403; using the same country ZIP inside the official Figshare release.', flush=True)
        downloads.append(fetch_country_from_figshare(metadata, 'RDD2022_United_States.zip'))
    (LOGS / 'downloads.json').write_text(json.dumps(downloads, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(downloads, ensure_ascii=False, indent=2), flush=True)

if __name__ == '__main__':
    main()
