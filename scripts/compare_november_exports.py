"""Compare Label Studio answers or assistant-assisted results for the November batch."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from check_november_annotations import parse_answer


def region_value(answer, from_name):
    return [r['value'] for r in answer['result'] if r.get('from_name') == from_name]


def task_digest(data):
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(payload.encode('utf-8')).hexdigest()


def records(path, expected, road, prediction_drafts=False):
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    items, seen = {}, set()
    for task in raw:
        task_data = task.get('data', {})
        item_id = task_data.get('item_id')
        if item_id not in expected or item_id in seen:
            raise ValueError(f'{path.name}: unexpected or repeated item {item_id!r}')
        seen.add(item_id)
        field = 'image' if road else 'text'
        if prediction_drafts and set(task_data) == {'item_id','task_sha256'}:
            same_material = task_data['task_sha256'] == task_digest(expected[item_id])
        else:
            same_material = task_data.get(field) == expected[item_id][field]
        if not same_material:
            raise ValueError(f'{path.name}: material changed for {item_id}')
        if prediction_drafts:
            accepted = task.get('predictions', [])
            if task.get('annotations') or len(accepted) != 1 or not accepted[0].get('model_version'):
                raise ValueError(f'{path.name}: expected one named assistant result for {item_id}')
        else:
            accepted = [a for a in task.get('annotations', []) if not a.get('was_cancelled') and not a.get('ground_truth')]
        if len(accepted) > 1:
            raise ValueError(f'{path.name}: choose a single original answer for {item_id}')
        if not accepted:
            continue
        answer, = accepted
        checked = parse_answer(json.dumps(answer['result']), road)
        checked['labels'] = region_value(answer,'damage' if road else 'label')
        checked['copied_prediction_flag'] = bool(answer.get('parent_prediction') or answer.get('parent_prediction_id'))
        items[item_id] = checked
    return items


def iou(a, b):
    xa, ya, wa, ha = [a[k] for k in ('x','y','width','height')]
    xb, yb, wb, hb = [b[k] for k in ('x','y','width','height')]
    intersection = max(0,min(xa+wa,xb+wb)-max(xa,xb))*max(0,min(ya+ha,yb+hb)-max(ya,yb))
    return intersection/(wa*ha+wb*hb-intersection)


def match_boxes(a, b):
    ranked = [[(j, iou(aa,bb)) for j,bb in enumerate(b)] for aa in a]
    edges = [sorted((p for p in row if p[1]>=0.5), key=lambda z:(-z[1],z[0])) for row in ranked]
    matched = {}

    def assign(i, visited):
        for j, score in edges[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in matched or assign(matched[j][0],visited):
                matched[j]=(i,score)
                return True
        return False

    for i in sorted(range(len(a)),key=lambda j:(len(edges[j]),j)):
        assign(i,set())
    return [(i,j,score) for j,(i,score) in sorted(matched.items())]


def compare(a_path,b_path,tasks_path,road,prediction_drafts=False):
    tasks=json.loads(tasks_path.read_text(encoding='utf-8-sig'))
    if any(set(task)!={'data'} for task in tasks):
        raise ValueError('Use the blind task manifest as the reference')
    expected={task['data']['item_id']:task['data'] for task in tasks}
    if len(expected)!=len(tasks):
        raise ValueError('Duplicate IDs in the task manifest')
    a,b=(records(path,expected,road,prediction_drafts) for path in (a_path,b_path))
    common=sorted(a.keys()&b.keys())
    valid=[item for item in common if a[item]['valid'] and b[item]['valid']]
    report = {'expected_items':len(expected),'two_submissions':len(common),'two_valid_submissions':len(valid),
              'not_valid_in_both':sorted(set(common)-set(valid)),'missing_A':sorted(expected.keys()-a.keys()),
              'missing_B':sorted(expected.keys()-b.keys()), 'review_items':[]}
    for name, entries in (('A',a),('B',b)):
        times=[row['seconds'] for row in entries.values() if row['seconds'] is not None]
        report[name] = {'submitted':len(entries),'valid':sum(r['valid'] for r in entries.values()),
                        'measured_items':len(times),'active_seconds':sum(times) if times else None,
                        'review_states':dict(Counter(r['review'] for r in entries.values())),
                        'known_prediction_copy_ids':[k for k,v in entries.items() if v['copied_prediction_flag']]}
    if road:
        matched, same, count_a, count_b = 0,0,0,0
        scores=[]
        for item in valid:
            la,lb=a[item]['labels'],b[item]['labels']
            pairs=match_boxes(la,lb)
            nclass=sum(la[i]['rectanglelabels']==lb[j]['rectanglelabels'] for i,j,_ in pairs)
            matched+=len(pairs)
            same+=nclass
            count_a+=len(la)
            count_b+=len(lb)
            scores.extend(score for _,_,score in pairs)
            if len(la)!=len(pairs) or len(lb)!=len(pairs) or len(pairs)!=nclass:
                report['review_items'].append({'id':item,'boxes_A':len(la),'boxes_B':len(lb),'matched':len(pairs),'same_class':nclass})
        report['agreement'] = {'matched_boxes':matched,'unmatched_A':count_a-matched,'unmatched_B':count_b-matched,
                'same_class_matched':same,'class_agreement':same/matched if matched else None,
                'mean_iou':sum(scores)/len(scores) if scores else None,'iou_threshold':0.5}
    else:
        pairs=Counter((a[item]['labels'][0]['choices'][0],b[item]['labels'][0]['choices'][0]) for item in valid)
        report['review_items']=[{'id':item,'A':a[item]['labels'][0]['choices'][0],'B':b[item]['labels'][0]['choices'][0]}
                                 for item in valid if a[item]['labels']!=b[item]['labels']]
        n=len(valid)
        agreement = sum(pairs[c,c] for c in ('ham','spam'))/n if n else None
        expected_agreement=(sum(sum(pairs[c,k] for k in ('ham','spam')) * sum(pairs[k,c] for k in ('ham','spam'))
                               for c in ('ham','spam')) / n**2) if n else None
        kappa=(agreement-expected_agreement)/(1-expected_agreement) if n and expected_agreement<1 else None
        report['agreement']={'fraction':agreement,'cohen_kappa':kappa,
                             'counts':{f'{x}/{y}':pairs[x,y] for x in ('ham','spam') for y in ('ham','spam')}}
    report['note']='Metrics describe the submitted pair, not adjudicated truth. Independence must be confirmed by the participants.'
    if prediction_drafts:
        report['paired_predictions'] = report.pop('two_submissions')
        report['paired_valid_predictions'] = report.pop('two_valid_submissions')
        for participant in ('A','B'):
            report[participant]['assistant_items'] = report[participant].pop('submitted')
        report['note'] = 'Agreement between the separate assistant-assisted passes from participants A and B.'
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=['text','road'])
    parser.add_argument('a',type=Path)
    parser.add_argument('b',type=Path)
    parser.add_argument('tasks',type=Path)
    parser.add_argument('--assistant','--prediction-drafts',dest='assistant',action='store_true',
                        help='Compare assistant-assisted results stored under predictions')
    args=parser.parse_args()
    if args.a.samefile(args.b):
        parser.error('A and B must be distinct exports')
    print(json.dumps(compare(args.a,args.b,args.tasks,args.kind=='road',args.assistant),ensure_ascii=False,indent=2))
