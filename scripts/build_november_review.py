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
OUT = ROOT / 'outputs/nov18_draft'

# Reviewed 1-based rows from the local reading pool. These are Codex suggestions.
SPAM = {9, 12, 13, 14, 16, 20, 21, 23, 24, 25, 26, 27, 29}
HAM = {1, 6, 7, 17, 18, 19, 32, 33, 34, 35, 36, 37, 39, 40, 41, 44, 45, 46,
       47, 48, 49, 50, 52, 53, 54, 57, 59, 60, 62, 63, 64, 67, 68, 69, 70, 71,
       73, 75, 76, 77, 78, 79, 80, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92,
       93, 94, 95, 96, 98, 99, 100}
EXTRA_SPAM = {29, 51, 82, 83, 88, 107, 124, 145, 156, 165}
TEXT_RECHECK = {29, 83, 88, 156, 165}
ROAD_EXCLUDE = {12, 27, 29, 45, 48, 65}
ROAD_NOTES = {
    1:'Проверь дальние крошечные D40: потеря материала должна быть видна.',
    6:'Две рамки перекрываются на линейном дефекте. Проверь, одна ли это непрерывная трещина.',
    11:'Проверь, не продублированы ли ветви сетки D20 отдельными D00/D10.',
    14:'Проверь, есть ли углубление, или это круглая заплатка.',
    18:'Проверь, где заканчивается отдельная трещина и начинается повреждение D40.',
    23:'Внутри сетки есть мелкие D40. Проверь, действительно ли отсутствует материал.',
    26:'Проверь сетку: отдельная линия или группа замкнутых ячеек?',
    31:'Поперечный шов рядом с повреждением. Не включай технологический шов в рамку.',
    34:'Проверь, нужно ли объединить две соприкасающиеся области D20.',
    35:'Сравни по участку с исключёнными IMG-M045 и IMG-M048. Проверь мелкие D40.',
    39:'Рамки мелкие: увеличь снимок и проверь границы каждого объекта.',
    41:'Проверь край шва моста: отдели настоящую выбоину от шва.',
    44:'Виден участок длинной трещины вне рамок. Проверь полноту разметки.',
    47:'Отличи глубокое разрушение от длинной трещины или колеи.',
    49:'Лужа может скрывать края. Используй видимую границу повреждения.',
    53:'Две крупные D40 перекрываются. По codebook это может быть сетка или трещина у разметки.',
    55:'Крупный план с отличающимся ракурсом; проверь соответствие набору.',
    57:'Проверь D40: видимая сетка трещин сама по себе не доказывает выбоину.',
    58:'Проверь нижнюю рамку D40: удлинённый разрыв и потеря материала различаются.',
    61:'Дальний дефект на ремонтируемом участке; при неразличимости отправь на разбор.',
    63:'Мокрая обочина: отличи повреждение от лужи и проверь границы.',
}


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

    def add(text, meta, label, reason, priority=False):
        hash_text = sha256(text.encode())
        normalized = re.sub(r'[^a-z0-9]+',' ',text.casefold()).strip()
        if hash_text in seen or normalized in seen_norm:
            return
        seen.add(hash_text)
        seen_norm.add(normalized)
        selection.append({'data': {'item_id': meta['item_id'], 'text':text},
             'manifest': dict(meta,status='draft_pending_human_review'),
             'suggestion':{'label':label,'reason':reason,'priority':priority,
                           'source':'Codex, text read against codebook 0.1'}})

    for row in sorted(HAM | SPAM):
        source = pool[row-1]
        text = source['text']
        h = sha256(text.encode())
        manifest = by_hash.get(h) or dict(source_ref,item_id=f'TXT-M{180+row:03}',content_sha256=h,
                   group_id=source['video_id'],source_record_id=source['comment_id'])
        if row in SPAM:
            label, reason = 'spam', 'Есть самопродвижение: просьба посмотреть свой контент, зайти на страницу или подписаться.'
        else:
            label, reason = 'ham', 'Реакция на песню, клип или обсуждение; явного стороннего продвижения нет.'
        if row in {6,7,17,18,19,33,68}:
            reason = 'Упоминание подписки, розыгрыша или лайка нужно отличить от собственного рекламного призыва.'
        add(text,manifest,label,reason,row in {6,7,17,18,19,33,68})
    for num in sorted(EXTRA_SPAM):
        item_id = f'TXT-M{num:03}'
        proposed = 'Предложение перейти к стороннему контенту или поддержать продвижение; проверь контекст, ссылку не открывай.'
        add(tasks[item_id]['text'],old[item_id],'spam',proposed,num in TEXT_RECHECK)
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
            'suggestion':{'boxes':box_list,'reason':ROAD_NOTES.get(idx,'Проверь все рамки и пропуски при исходном размере.'),
                          'priority':idx in ROAD_NOTES,'source':'EPDD YOLO source labels mapped to RDD2022 classes'}})
    return selection


def prediction(row, kind):
    data, suggestion, meta = row['data'],row['suggestion'],row['manifest']
    results = []
    if kind == 'text':
        results.append(dict(id='text-draft',from_name='label',to_name='text',type='choices',
                            value={'choices':[suggestion['label']]}))
    else:
        for n,(label,x,y,right,bottom) in enumerate(suggestion['boxes'],1):
            results.append(dict(id=f'draft-box-{n}', from_name='damage',to_name='image',type='rectanglelabels',
                 original_width=meta['width'],original_height=meta['height'],image_rotation=0,
                 value=dict(x=x,y=y,width=round(right-x,5),height=round(bottom-y,5),rotation=0,rectanglelabels=[label])))
    return {'data':data, 'predictions':[{'model_version':'Codex draft' if kind=='text' else 'EPDD source draft', 'result':results}]}


def build():
    text,road = build_text(),build_road()
    sections = {'text':text,'road':road}
    for kind,rows in sections.items():
        assert len(rows) >= 50 and len({r['data']['item_id'] for r in rows}) == len(rows)
        write_json(WORK/f'{kind}_blind_tasks.json',[{'data':r['data']} for r in rows])
        write_json(WORK/f'{kind}_review_tasks.json',[prediction(r,kind) for r in rows])
        write_json(WORK/f'{kind}_manifest.json',[r['manifest'] for r in rows])
        write_json(WORK/f'{kind}_suggestions.json',[dict(r['suggestion'],item_id=r['data']['item_id']) for r in rows])
    summary = {'text_items':len(text), 'text_draft_classes':dict(Counter(r['suggestion']['label'] for r in text)),
          'road_items':len(road), 'road_draft_boxes':dict(Counter(b[0] for r in road for b in r['suggestion']['boxes'])),
          'road_images_by_draft_class': dict(Counter(c for r in road for c in {b[0] for b in r['suggestion']['boxes']})),
          'road_excluded_ids':[f'IMG-M{i:03}' for i in sorted(ROAD_EXCLUDE)], 'scope':'review_draft',
          'draft_is_human_annotation':False,'human_submissions_created':0}
    write_json(OUT/'draft_summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    build()
