"""Build November review queues of external real data without importing source labels."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import re
import struct
import zipfile
import zlib

from PIL import Image, ImageOps

from download_data import RemoteZipIndex
from prepare_annotation_pilot import ROOT, read_csv, write_json
from prepare_image_candidates import dhash, extract_member

CANDIDATES = ROOT / 'data/manual/local/candidates'
MEDIA = ROOT / 'annotation/pilot/local/images/november'
OUT = CANDIDATES / 'november_2026'
DATE = '2026-11-17'
TEXT_URL = 'https://huggingface.co/datasets/hridaydutta123/YT-100K'
ROAD_URL = 'https://zenodo.org/records/20454801'
BASE_VIDEOS = {'9bZkp7q19f0', 'CevxZvSJLk8', 'KQ6zr6kCPj8', 'uelHwf8o7_U', 'pRpeEdMmmQ0'}
PROMO = re.compile(r'subscribe|check\s+(?:out\s+)?my|visit\s+my|my\s+channel|https?://|www\.|telegram|whatsapp|giveaway|earn money', re.I)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def text_queue():
    old = {row['text_sha256'] for row in read_csv(ROOT / 'data/processed/youtube_profile.csv')}
    rows = [[], []]
    seen = set(old)
    with (CANDIDATES / 'YT-100K.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream, delimiter='\t'):
            vid, cid = row.get('video_id'), row.get('comment_id')
            if row.get('category') != 'music' or not vid or not cid or vid in BASE_VIDEOS:
                continue
            text = (row.get('comment') or '').strip()
            if not re.search(r'[A-Za-z]{3}', text) or len(text) > 700:
                continue
            h = digest(text.encode())
            if h in seen:
                continue
            seen.add(h)
            rows[bool(PROMO.search(text))].append((text, h, vid, cid))
    rng = random.Random(42)
    chosen = rng.sample(rows[0], 65) + rng.sample(rows[1], 115)
    rng.shuffle(chosen)
    manifest, tasks = [], []
    for i, (comment, h, video_id, comment_id) in enumerate(chosen, 1):
        item_id = f'TXT-M{i:03}'
        manifest.append(dict(item_id=item_id, content_sha256=h, group_id=video_id,
            source_record_id=comment_id, source_url=TEXT_URL, source_author='H. S. Dutta; B. Khan',
            source_doi='10.57967/hf/3602', license_or_permission='CC BY 4.0 per source card',
            accessed_at=DATE, status='candidate_pending_human_review'))
        tasks.append({'data': {'item_id': item_id, 'text': comment}})
    write_json(OUT / 'youtube_manifest.json', manifest)
    write_json(OUT / 'youtube_tasks.json', tasks)
    return {'text_candidates': len(tasks), 'source_videos': len({row['group_id'] for row in manifest}),
            'baseline_exact_matches': 0, 'without_keyword': 65, 'with_keyword': 115}


def label_span(remote, infos):
    """Read the contiguous label area once; no boxes are kept or passed to annotators."""
    first = min(i.header_offset for i in infos)
    last = max(i.header_offset + i.compress_size + 2048 for i in infos)
    if last - first > 4_000_000 or last > remote.size:
        raise ValueError('Unexpected label span')
    with remote.range_response(first, last - 1) as response:
        block = response.read(last - first + 1)
    if len(block) != last - first:
        raise ValueError('Incomplete label span')
    by_image = {}
    for info in infos:
        off = info.header_offset - first
        if block[off:off+4] != b'PK\x03\x04':
            raise ValueError('Invalid label header')
        name, extra = struct.unpack_from('<HH', block, off + 26)
        start = off + 30 + name + extra
        packed = block[start:start+info.compress_size]
        raw = zlib.decompress(packed, -15) if info.compress_type == zipfile.ZIP_DEFLATED else packed
        if len(raw) != info.file_size or zlib.crc32(raw) & 0xffffffff != info.CRC:
            raise ValueError('Label CRC mismatch')
        by_image[Path(info.filename).stem] = {int(line.split()[0]) for line in raw.splitlines() if line.strip()}
    return by_image


def road_queue(count):
    import yaml

    meta = json.loads((CANDIDATES / 'ecupdd_metadata.json').read_bytes())
    if meta['doi'] != '10.5281/zenodo.20454801' or meta['metadata']['license']['id'] != 'cc-by-4.0':
        raise ValueError('Source and license must be rechecked')
    f = next(f for f in meta['files'] if f['key'] == 'EPDD.zip')
    url, size = f['links']['self'], f['size']
    remote = RemoteZipIndex(url, size)
    with zipfile.ZipFile(remote) as archive:
        infos = archive.infolist()
    yml = yaml.safe_load(extract_member(remote, next(i for i in infos if i.filename == 'EPDD/data.yaml')))
    classes = yml['names']
    if isinstance(classes, list):
        classes = dict(enumerate(classes))
    print('EPDD classes:',classes,flush=True)
    codes = {}
    for idx, name in classes.items():
        name = str(name).casefold()
        for key, code in [('longitud','D00'),('transvers','D10'),('alligator','D20'),('pothole','D40'),('bache','D40'),('cocodrilo','D20')]:
            if key in name:
                codes[int(idx)] = code
    if set(codes.values()) != {'D00','D10','D20','D40'}:
        raise ValueError(f'Unmapped source classes: {classes}')
    labels = label_span(remote, [i for i in infos if i.filename.startswith('EPDD/train/labels/') and i.filename.endswith('.txt')])
    images = [i for i in infos if i.filename.startswith('EPDD/train/images/') and Path(i.filename).suffix.lower() in {'.jpg','.jpeg','.png'}]
    rng = random.Random(42)
    rng.shuffle(images)
    old = read_csv(ROOT / 'data/processed/rdd_us_images.csv')
    byte_hashes = {r['sha256'] for r in old}
    pixel_hashes = {r['pixel_sha256'] for r in old}
    image_hashes = [int(r['dhash'], 16) for r in old]
    picked, reserved, quotas = [], set(), Counter()
    for idx, code in sorted(codes.items(), key=lambda kv: sum(kv[0] in l for l in labels.values())):
        for info in images:
            if quotas[code] >= count + 6:
                break
            if info.filename in reserved or idx not in labels.get(Path(info.filename).stem, set()):
                continue
            picked.append((code, info))
            quotas[code] += 1
            reserved.add(info.filename)

    def retrieve(info):
        import time
        for attempt in range(3):
            try:
                raw = extract_member(RemoteZipIndex(url, size), info)
                with Image.open(io.BytesIO(raw)) as source:
                    im = ImageOps.exif_transpose(source).convert('RGB')
                    stream = io.BytesIO()
                    im.save(stream, format='JPEG', quality=94)
                    return raw, digest(im.tobytes()), stream.getvalue(), im.size, dhash(im)
            except (OSError, ValueError) as error:
                if attempt == 2:
                    print('Skipped',Path(info.filename).name,type(error).__name__,flush=True)
                    return None
                time.sleep(attempt + 1)

    picked.sort(key=lambda pair: (pair[0], images.index(pair[1])))
    MEDIA.mkdir(parents=True, exist_ok=True)
    kept, tasks, classes_kept, road_hashes = [], [], Counter(), []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (code, info), loaded in zip(picked, pool.map(retrieve, (i for _, i in picked))):
            if loaded is None or classes_kept[code] >= count:
                continue
            raw,pix,jpeg,dimensions,ph = loaded
            if digest(raw) in byte_hashes or pix in pixel_hashes or any((ph ^ h).bit_count() <= 3 for h in image_hashes + road_hashes):
                continue
            classes_kept[code] += 1
            pixel_hashes.add(pix)
            road_hashes.append(ph)
            kept.append((info, digest(raw), pix, jpeg, dimensions, ph))
    rng.shuffle(kept)
    manifest = []
    for i, (info, source_sha, pix, jpeg, (w,h), ph) in enumerate(kept,1):
        item_id = f'IMG-M{i:03}'
        dest = MEDIA / f'{item_id}.jpg'
        dest.write_bytes(jpeg)
        manifest.append(dict(item_id=item_id, content_sha256=digest(jpeg), original_sha256=source_sha,
            pixel_sha256=pix, width=w, height=h, source_record_id=info.filename,
            group_id='EPDD: участок уточняется при отборе',
            file_path=str(dest.relative_to(ROOT).as_posix()), source_url=ROAD_URL,
            source_author='Leonardo Zambrano', source_doi=meta['doi'], license_or_permission='CC BY 4.0',
            accessed_at=DATE, status='candidate_pending_human_review',
            preprocessing='EXIF orientation applied, converted to RGB JPEG, metadata removed'))
        tasks.append({'data': {'item_id': item_id, 'image': f'/data/local-files/?d=images/november/{item_id}.jpg'}})
    write_json(OUT / 'rdd_manifest.json', manifest)
    write_json(OUT / 'rdd_tasks.json', tasks)
    return {'road_candidates': len(tasks), 'source_selection_strata': dict(classes_kept),
            'known_exact_or_near_matches_in_output': 0}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=('text', 'road'))
    cli.add_argument('--per-class', type=int, default=16)
    args = cli.parse_args()
    target = OUT / ('youtube_tasks.json' if args.mode == 'text' else 'rdd_tasks.json')
    if target.exists():
        cli.error(f'Existing tasks preserved: {target}')
    summary = text_queue() if args.mode == 'text' else road_queue(args.per_class)
    write_json(OUT / (args.mode + '_summary.json'), summary)
    print(json.dumps(summary, ensure_ascii=False))
