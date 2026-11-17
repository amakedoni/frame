"""Fetch a small, licensed candidate queue. Candidates are not manual collection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import struct
import urllib.request
import zipfile
import zlib

from PIL import Image

from download_data import RemoteZipIndex
from prepare_annotation_pilot import ROOT, read_csv, write_json

METADATA_URL = 'https://api.figshare.com/v2/articles/21214400'


def extract_member(remote: RemoteZipIndex, info: zipfile.ZipInfo) -> bytes:
    if info.flag_bits & 1 or info.file_size > 20 * 1024**2:
        raise ValueError('Encrypted or unexpectedly large image')
    remote.seek(info.header_offset)
    header = remote.read(30)
    if header[:4] != b'PK\x03\x04':
        raise ValueError('Invalid ZIP local header')
    name_length, extra_length = struct.unpack_from('<HH', header, 26)
    start = info.header_offset + 30 + name_length + extra_length
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError('Unsupported ZIP compression')
    with remote.range_response(start, start + info.compress_size - 1) as response:
        packed = response.read(info.compress_size + 1)
    if len(packed) != info.compress_size:
        raise ValueError('Wrong compressed length')
    content = zlib.decompress(packed, -15) if info.compress_type == zipfile.ZIP_DEFLATED else packed
    if len(content) != info.file_size or zlib.crc32(content) & 0xffffffff != info.CRC:
        raise ValueError('Extracted member failed length or CRC check')
    return content


def dhash(image: Image.Image) -> int:
    pixels = image.convert('L').resize((9, 8)).load()
    value = 0
    for y in range(8):
        for x in range(8):
            value = value << 1 | (pixels[x + 1, y] > pixels[x, y])
    return value


def prepare(count: int = 12, output: Path | None = None) -> dict:
    if not 1 <= count <= 100:
        raise ValueError('Candidate count must be between 1 and 100')
    output = output or ROOT / 'data/manual/local/candidates/figshare_21214400_v3'
    if output.exists():
        raise FileExistsError(f'{output} exists; existing files are preserved')
    with urllib.request.urlopen(METADATA_URL, timeout=30) as response:
        metadata = json.load(response)
    if metadata.get('version') != 3 or metadata['license']['name'] != 'CC BY 4.0':
        raise ValueError('Source version or license changed. Review metadata before download.')
    source_file = next(f for f in metadata['files'] if f['id'] == 37622126)
    remote = RemoteZipIndex(source_file['download_url'], source_file['size'])
    with zipfile.ZipFile(remote) as archive:
        members = sorted([i for i in archive.infolist()
            if i.filename.lower().endswith(('.jpg', '.jpeg', '.png'))
            and not i.filename.startswith('__MACOSX')], key=lambda i: i.filename)
    if len(members) < count:
        raise ValueError('Not enough image members')
    # Evenly spaced source files, without inspecting the source's ready-made labels.
    selected = [members[i * len(members) // count] for i in range(count)]
    baseline = read_csv(ROOT / 'data/processed/rdd_us_images.csv')
    old_pixels = {r['pixel_sha256'] for r in baseline}
    old_dhashes = [(r['image_id'], int(r['dhash'], 16)) for r in baseline]
    output.mkdir(parents=True)
    write_json(output / 'source_metadata.json', metadata)
    checked_at = datetime.now(timezone.utc).isoformat()
    authors = '; '.join(a['full_name'] for a in metadata['authors'])
    manifest = []
    for index, info in enumerate(selected, 1):
        content = extract_member(remote, info)
        candidate_id = f'EXT-IMG-{index:03}'
        suffix = Path(info.filename).suffix.lower()
        filename = candidate_id + suffix
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            rgb = image.convert('RGB')
            width, height = rgb.size
            pixel_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
            hash_value = dhash(rgb)
            nearest_id, distance = min(((name, (hash_value ^ old).bit_count())
                for name, old in old_dhashes), key=lambda p: (p[1], p[0]))
        (output / filename).write_bytes(content)
        manifest.append({
            'candidate_id': candidate_id, 'path': filename,
            'source_url': metadata['url_public_html'], 'source_doi': metadata['doi'],
            'source_file_id': source_file['id'], 'archive_member': info.filename,
            'author': authors, 'license': metadata['license']['name'],
            'license_url': metadata['license']['url'], 'accessed_at': checked_at,
            'capture_date': None, 'width': width, 'height': height,
            'sha256': hashlib.sha256(content).hexdigest(), 'pixel_sha256': pixel_hash,
            'exact_pixel_match_baseline': pixel_hash in old_pixels,
            'nearest_rdd_image': nearest_id, 'nearest_rdd_dhash_distance': distance,
            'review_status': 'pending_human_review', 'reviewer': None,
            'accepted_manual_example': False, 'target_class': None,
            'group_id': 'figshare21214400-route-unresolved',
            'review_notes': 'Resolve physical-road groups before counting independent examples.',
            'notes': 'Automatic candidate retrieval. Human suitability, novelty and class review pending.',
        })
        write_json(output / 'manifest.json', manifest)
        print(f'{candidate_id}: {width}x{height}, nearest dHash distance {distance}', flush=True)
    summary = {'candidate_count': len(manifest), 'accepted_manual_examples': 0,
        'source_url': metadata['url_public_html'], 'source_doi': metadata['doi'],
        'source_license': metadata['license']['name'], 'checked_at': checked_at,
        'bytes_downloaded_images': sum((output / r['path']).stat().st_size for r in manifest),
        'full_archive_bytes': source_file['size'],
        'exact_baseline_matches': sum(r['exact_pixel_match_baseline'] for r in manifest),
        'possible_near_matches': sum(r['nearest_rdd_dhash_distance'] <= 3 for r in manifest),
        'note': 'Only candidates; no manual collection, target labels or human annotations claimed.'}
    write_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=12)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.count, args.output), ensure_ascii=False, indent=2))
