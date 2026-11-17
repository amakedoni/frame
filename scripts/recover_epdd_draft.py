"""Fetch original EPDD boxes for a private review draft, never for the blind pass."""
import hashlib
import io
import json
import random
import struct
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from download_data import RemoteZipIndex
from prepare_image_candidates import dhash
from prepare_november_candidates import CANDIDATES, OUT, ROOT, ROAD_URL, DATE

DRAFT = ROOT / 'data/manual/local/review_draft'
MEDIA = ROOT / 'annotation/pilot/local/images/november_review'
CODES = {0: 'D40', 2: 'D00', 3: 'D10', 5: 'D20'}


def unpack_member(data, offset, info):
    if data[offset:offset+4] != b'PK\x03\x04':
        raise ValueError('Wrong ZIP member header')
    name, extra = struct.unpack_from('<HH', data, offset + 26)
    start = offset + 30 + name + extra
    packed = data[start:start+info.compress_size]
    if info.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
        raise ValueError('Unexpected ZIP compression')
    raw = zlib.decompress(packed, -15) if info.compress_type == zipfile.ZIP_DEFLATED else packed
    if len(raw) != info.file_size or zlib.crc32(raw) & 0xffffffff != info.CRC:
        raise ValueError('ZIP member integrity check failed')
    return raw


def read_range(remote, first, end):
    with remote.range_response(first, end-1) as result:
        value = result.read(end-first+1)
    if len(value) != end-first:
        raise ValueError('Incomplete ZIP range')
    return value


def parse_boxes(raw):
    result = []
    for row in raw.decode().splitlines():
        if not row.strip():
            continue
        label, x, y, w, h = map(float, row.split())
        if label not in CODES:
            continue
        left, top, right, bottom = x-w/2, y-h/2, x+w/2, y+h/2
        if not (-0.002 <= left < right <= 1.002 and -0.002 <= top < bottom <= 1.002):
            continue
        corners = [round(100*min(1,max(0,v)),5) for v in (left, top, right, bottom)]
        if corners[0] < corners[2] and corners[1] < corners[3]:
            result.append([CODES[label],*corners])
    return result


def fetch():
    DRAFT.mkdir(exist_ok=True, parents=True)
    MEDIA.mkdir(exist_ok=True, parents=True)
    source = json.loads((CANDIDATES/'ecupdd_metadata.json').read_bytes())
    assert source['doi'] == '10.5281/zenodo.20454801' and source['metadata']['license']['id'] == 'cc-by-4.0'
    f = next(i for i in source['files'] if i['key'] == 'EPDD.zip')
    remote = RemoteZipIndex(f['links']['self'], f['size'])
    with zipfile.ZipFile(remote) as archive:
        infos = archive.infolist()
    yaml_info = next(i for i in infos if i.filename == 'EPDD/data.yaml')
    cfg = read_range(remote, yaml_info.header_offset, yaml_info.header_offset+yaml_info.compress_size+1024)
    import yaml
    names = yaml.safe_load(unpack_member(cfg, 0, yaml_info))['names']
    assert [names[i].casefold() for i in CODES] == ['pothole','longitudinal_crack','transverse_crack','alligator_crack']
    ann = [i for i in infos if i.filename.startswith('EPDD/train/labels/') and not i.is_dir()]
    first = min(i.header_offset for i in ann)
    end = max(i.header_offset + i.compress_size + 2048 for i in ann)
    assert end-first < 4_000_000
    block = read_range(remote, first, end)
    boxes = {Path(i.filename).stem:parse_boxes(unpack_member(block, i.header_offset-first, i)) for i in ann}
    current = json.loads((OUT/'rdd_manifest.json').read_bytes())
    names = {r['source_record_id'] for r in current}
    known = {int(row['dhash'],16) for row in __import__('prepare_november_candidates').read_csv(ROOT/'data/processed/rdd_us_images.csv')}
    for row in current:
        with Image.open(ROOT/row['file_path']) as im:
            known.add(dhash(im))
    candidates = [i for i in infos if i.filename.startswith('EPDD/train/images/') and not i.is_dir()
                  and i.filename not in names and 0<i.file_size<3_000_000
                  and any(b[0]=='D40' for b in boxes.get(Path(i.filename).stem,[]))]
    random.Random(171126).shuffle(candidates)
    positions = {a.header_offset:b.header_offset for a,b in zip(sorted(infos,key=lambda i:i.header_offset),sorted(infos,key=lambda i:i.header_offset)[1:])}

    def retrieve(info):
        try:
            local = RemoteZipIndex(f['links']['self'],f['size'])
            raw = unpack_member(read_range(local,info.header_offset,positions[info.header_offset]),0,info)
            with Image.open(io.BytesIO(raw)) as source_img:
                # Keep only neutral orientations so that source box transforms are unnecessary.
                if source_img.getexif().get(274,1) != 1:
                    return None
                im = ImageOps.exif_transpose(source_img).convert('RGB')
                h = dhash(im)
                out = io.BytesIO()
                im.save(out,'JPEG',quality=94)
                return out.getvalue(),h,im.size,hashlib.sha256(raw).hexdigest()
        except (OSError,ValueError) as err:
            print(f'Skipped one candidate: {err}',flush=True)
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = pool.map(retrieve,candidates[:35])
        for info,value in zip(candidates,pending):
            if value is None:
                continue
            image,h,(width,height),source_hash = value
            if min(((h^k).bit_count() for k in known),default=64)<=3:
                continue
            known.add(h)
            item_id=f'IMG-M{len(current)+1:03}'
            file=MEDIA/(item_id+'.jpg')
            file.write_bytes(image)
            current.append(dict(item_id=item_id,content_sha256=hashlib.sha256(image).hexdigest(),
                 original_sha256=source_hash,width=width,height=height,source_record_id=info.filename,
                 group_id='EPDD: участок уточняется при проверке', file_path=file.relative_to(ROOT).as_posix(),
                 source_url=ROAD_URL,source_author='Leonardo Zambrano',source_doi=source['doi'],
                 license_or_permission='CC BY 4.0',accessed_at=DATE,
                 preprocessing='RGB JPEG conversion, metadata removed',status='draft_pending_human_review'))
            if len(current)>=66:
                break
    packed = [{'item_id':r['item_id'],'source_record_id':r['source_record_id'],
            'boxes':boxes[Path(r['source_record_id']).stem]} for r in current]
    (DRAFT/'epdd_manifest.json').write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (DRAFT/'epdd_source_boxes.json').write_text(json.dumps(packed,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f'{len(current)} road photos, {sum(len(x["boxes"]) for x in packed)} source boxes recovered',flush=True)


if __name__ == '__main__':
    fetch()
