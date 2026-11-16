"""Count actual independent submissions and measured active time in local Label Studio."""
from collections import Counter
import json
from math import isfinite
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def box_valid(values):
    coords = tuple(values.get(key) for key in ('x', 'y', 'width', 'height'))
    if any(type(value) not in (int, float) or not isfinite(value) for value in coords):
        return False
    x, y, w, h = coords
    return (x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= 100.01 and y+h <= 100.01
            and values.get('rotation', 0) == 0
            and len(values.get('rectanglelabels', [])) == 1
            and values['rectanglelabels'][0] in {'D00','D10','D20','D40'})


def parse_answer(raw, is_image):
    values = json.loads(raw or '[]')
    by_name = {}
    for value in values:
        by_name.setdefault(value.get('from_name'), []).append(value.get('value', {}))
    review = by_name.get('review', [{}])[0].get('choices', [])
    seconds = by_name.get('time_seconds', [{}])[0].get('number')
    if type(seconds) not in (int, float) or not isfinite(seconds) or seconds <= 0:
        seconds = None
    marker = review[0] if len(review) == 1 else 'invalid'
    boxes = by_name.get('damage', [])
    label = by_name.get('label', [{}])[0].get('choices', [])
    if is_image:
        eligible = (marker == 'accept' and bool(boxes) and all(map(box_valid, boxes))) or (marker == 'no_damage' and not boxes)
    else:
        eligible = marker == 'accept' and len(label) == 1 and label[0] in {'ham', 'spam'}
    return {'review': marker, 'valid': bool(eligible), 'seconds': seconds}


def read_projects(annotator):
    path = ROOT / '.annotation-state' / annotator / 'label_studio.sqlite3'
    outcome = {}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for title, count in db.execute('select project.title, count(task.id) from project left join task on task.project_id=project.id group by project.id'):
            outcome[title] = {'task_count': count, 'answers': {}, 'conflicts': set()}
        for title, data, raw in db.execute('''
            select project.title, task.data, task_completion.result from task_completion
            join task on task.id=task_completion.task_id join project on task.project_id=project.id
            where not task_completion.was_cancelled and not task_completion.ground_truth
        '''):
            record = outcome[title]
            item = json.loads(data).get('item_id')
            if not item:
                continue
            if item in record['answers']:
                record['conflicts'].add(item)
            record['answers'][item] = parse_answer(raw, title.startswith('RDD'))
    return outcome


def summarize():
    a, b = read_projects('A'), read_projects('B')
    summary = {}
    for title in sorted(a.keys() | b.keys()):
        result, ids = {}, {}
        for tag, projects in [('A', a), ('B', b)]:
            p = projects.get(title, {'task_count': 0, 'answers': {}, 'conflicts': set()})
            answers = {k:v for k,v in p['answers'].items() if k not in p['conflicts']}
            ids[tag] = {k for k,v in answers.items() if v['valid']}
            times = [v['seconds'] for v in answers.values() if v['seconds'] is not None]
            result[tag] = {'tasks':p['task_count'], 'submitted_items':len(answers),
                 'valid_labels':len(ids[tag]), 'measured_items':len(times), 'active_seconds':sum(times) if times else None,
                 'duplicate_submission_items':len(p['conflicts']),
                 'reviews':dict(Counter(v['review'] for v in answers.values()))}
        result['valid_same_items_a_b'] = len(ids['A'] & ids['B'])
        summary[title] = result
    return summary


if __name__ == '__main__':
    print(json.dumps(summarize(), ensure_ascii=False, indent=2))
