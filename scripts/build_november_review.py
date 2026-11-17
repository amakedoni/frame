"""Prepare aligned blind tasks and a separate, explicitly sourced review draft."""
from collections import Counter
import hashlib
from pathlib import Path
import json
import random
import re

from prepare_annotation_pilot import ROOT, read_csv, write_json

INPUT = ROOT / 'data/manual/local/candidates/november_2026'
WORK = ROOT / 'data/manual/local/review_draft'
OUT = ROOT / 'outputs/nov18_materials'

# Reviewed 1-based positions in the local reading pool, without class assignments.
READING_SELECTION = {1, 6, 7, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 29,
       32, 33, 34, 35, 36, 37, 39, 40, 41, 44, 45, 46,
       47, 48, 49, 50, 52, 53, 54, 57, 59, 60, 62, 63, 64, 67, 68, 69, 70, 71,
       73, 75, 76, 77, 78, 79, 80, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92,
       93, 94, 95, 96, 98, 99, 100}
EXTRA_TEXT_ITEMS = {29, 51, 82, 83, 88, 107, 124, 145, 156, 165}
ROAD_EXCLUDE = {12, 27, 29, 45, 48, 65}


def load_json(path):
    return json.loads(path.read_bytes())


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def build_text():
    tasks = {r['data']['item_id']: r['data'] for r in load_json(INPUT/'youtube_tasks.json')}
    old = {r['item_id']: r for r in load_json(INPUT/'youtube_manifest.json')}
    by_hash = {r['content_sha256']:r for r in old.values()}
    seen = {r['text_sha256'] for r in read_csv(ROOT/'data/processed/youtube_profile.csv')}
    seen_norm = set()
    source_ref = next(iter(old.values()))
    selection = []
    pool = load_json(WORK/'text_reading_pool.json')

    def add(text, meta):
        hash_text = sha256(text.encode())
        normalized = re.sub(r'[^a-z0-9]+',' ',text.casefold()).strip()
        if hash_text in seen or normalized in seen_norm:
            return
        seen.add(hash_text)
        seen_norm.add(normalized)
        selection.append({'data': {'item_id': meta['item_id'], 'text':text},
             'manifest': dict(meta,status='draft_pending_human_review')})

    for row in sorted(READING_SELECTION):
        source = pool[row-1]
        text = source['text']
        h = sha256(text.encode())
        manifest = by_hash.get(h) or dict(source_ref,item_id=f'TXT-M{180+row:03}',content_sha256=h,
                   group_id=source['video_id'],source_record_id=source['comment_id'])
        add(text,manifest)
    for num in sorted(EXTRA_TEXT_ITEMS):
        item_id = f'TXT-M{num:03}'
        add(tasks[item_id]['text'],old[item_id])
    random.Random(171126).shuffle(selection)
    return selection


def build_road():
    manifest = load_json(WORK/'epdd_manifest.json')
    boxes = {x['item_id']:x['boxes'] for x in load_json(WORK/'epdd_source_boxes.json')}
    selection = []
    for meta in manifest:
        item = meta['item_id']
        idx = int(item.removeprefix('IMG-M'))
        if idx in ROAD_EXCLUDE:
            continue
        photo = ROOT/meta['file_path']
        assert sha256(photo.read_bytes()) == meta['content_sha256']
        box_list = boxes[item]
        assert box_list and all(0 <= b[1] < b[3] <= 100 and 0 <= b[2] < b[4] <= 100 for b in box_list)
        image_route = photo.relative_to(ROOT/'annotation/pilot/local').as_posix()
        selection.append({'data': {'item_id':item,'image':f'/data/local-files/?d={image_route}'},
            'manifest':dict(meta,status='draft_pending_human_review'),
            'suggestion':{'boxes':box_list,'source':'EPDD YOLO source labels mapped to RDD2022 classes'}})
    return selection


def road_reference(row):
    data, suggestion, meta = row['data'],row['suggestion'],row['manifest']
    results = []
    for n,(label,x,y,right,bottom) in enumerate(suggestion['boxes'],1):
        results.append(dict(id=f'draft-box-{n}', from_name='damage',to_name='image',type='rectanglelabels',
             original_width=meta['width'],original_height=meta['height'],image_rotation=0,
             value=dict(x=x,y=y,width=round(right-x,5),height=round(bottom-y,5),rotation=0,rectanglelabels=[label])))
    return {'data':data, 'predictions':[{'model_version':'EPDD source boxes', 'result':results}]}


def build():
    text,road = build_text(),build_road()
    sections = {'text':text,'road':road}
    for kind,rows in sections.items():
        assert len(rows) >= 50 and len({r['data']['item_id'] for r in rows}) == len(rows)
        write_json(WORK/f'{kind}_blind_tasks.json',[{'data':r['data']} for r in rows])
        write_json(WORK/f'{kind}_manifest.json',[r['manifest'] for r in rows])
    write_json(WORK/'road_review_tasks.json',[road_reference(r) for r in road])
    write_json(WORK/'road_suggestions.json',[dict(r['suggestion'],item_id=r['data']['item_id']) for r in road])
    summary = {'text_items':len(text), 'text_labelled_items':0,
          'road_items':len(road), 'road_draft_boxes':dict(Counter(b[0] for r in road for b in r['suggestion']['boxes'])),
          'road_images_by_draft_class': dict(Counter(c for r in road for c in {b[0] for b in r['suggestion']['boxes']})),
          'road_excluded_ids':[f'IMG-M{i:03}' for i in sorted(ROAD_EXCLUDE)], 'scope':'review_draft',
          'draft_is_human_annotation':False,'human_submissions_created':0}
    write_json(OUT/'draft_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    build()
