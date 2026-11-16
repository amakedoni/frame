"""Initialize two local projects for one annotator. Never creates annotations."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def initialize_user(annotator: str) -> dict:
    directory = ROOT / '.annotation-state' / annotator
    directory.mkdir(parents=True, exist_ok=True)
    access_file = directory / 'access.json'
    if access_file.exists():
        access = json.loads(access_file.read_text(encoding='utf8'))
    else:
        access = {'username': f'annotator.{annotator.lower()}@coursework.local',
                  'password': secrets.token_urlsafe(24), 'token': secrets.token_hex(20)}
        access_file.write_text(json.dumps(access, indent=2) + '\n', encoding='utf8')
    marker = directory / 'user_initialized.json'
    if not marker.exists():
        executable = ROOT / '.annotation-venv/Scripts/label-studio.exe'
        environment = dict(os.environ, LABEL_STUDIO_ANALYTICS_ENABLED='false')
        with (directory / 'init.log').open('w', encoding='utf8') as log:
            subprocess.run([str(executable), 'init', '--data-dir', str(directory),
                '--username', access['username'], '--password', access['password'],
                '--user-token', access['token'], '--enable-legacy-api-token', '--quiet'],
                env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        marker.write_text('{"initialized":true}\n', encoding='utf8')
    return access


def configure(annotator: str, batch_kind: str = 'baseline') -> None:
    directory = ROOT / '.annotation-state' / annotator
    access = json.loads((directory / 'access.json').read_text(encoding='utf8'))
    base = 'http://127.0.0.1:' + ('8081' if annotator == 'A' else '8082')

    def request(method, path, body=None):
        headers = {'Authorization': 'Token ' + access['token']}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(base + path, method=method, headers=headers,
            data=json.dumps(body).encode('utf8') if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf8', errors='replace')[:1200]
            raise RuntimeError(f'Local API {method} {path}: HTTP {error.code}: {detail}') from None

    existing = request('GET', '/api/projects/')
    existing = existing['results'] if isinstance(existing, dict) else existing
    report = {'annotator': annotator, 'base_url': base, 'projects': []}
    if batch_kind == 'review' and annotator != 'A':
        raise ValueError('Review suggestions may only be imported into A’s separate review projects')
    batch, suffix = {
        'baseline': (ROOT / 'annotation/pilot/local', ' pilot'),
        'november': (ROOT / 'data/manual/local/candidates/november_2026', ' November candidates'),
        'blind': (ROOT / 'data/manual/local/review_draft', ' Blind selected'),
        'review': (ROOT / 'data/manual/local/review_draft', ' Review draft'),
    }[batch_kind]
    paths = {}
    for dataset in ('youtube', 'rdd'):
        if batch_kind in {'blind','review'}:
            kind = 'text' if dataset == 'youtube' else 'road'
            paths[dataset] = batch/f'{kind}_{batch_kind}_tasks.json'
        else:
            ending = '_combined_tasks.json' if batch_kind == 'november' and dataset == 'rdd' else '_tasks.json'
            paths[dataset] = batch / (dataset + ending)
    prepared = {key: json.loads(path.read_text(encoding='utf8')) for key, path in paths.items()}
    for tasks in prepared.values():
        if not tasks or any(set(task) != ({'data','predictions'} if batch_kind == 'review' else {'data'}) for task in tasks):
            raise ValueError('Batch was not built for the selected mode')
    for dataset, prefix in [('youtube', 'YouTube'), ('rdd', 'RDD')]:
        title = prefix + suffix
        matches = [p for p in existing if p['title'] == title]
        config = (ROOT / 'annotation/label_studio' / (dataset + '.xml')).read_text(encoding='utf8')
        if len(matches) > 1:
            raise ValueError('Duplicate project titles: resolve explicitly before import')
        if matches:
            project = matches[0]
        else:
            description = {
                'baseline':'Baseline pilot. Independent human annotation pending.',
                'november':'External real data. Suitability and independent human annotation pending.',
                'blind':'Same selected items for A and B. Do not open the draft before submitting blind answers.',
                'review':'Draft hints for verification only; these are not independent human annotations.',
            }[batch_kind]
            project = request('POST', '/api/projects/', {'title': title, 'description':description,
                'show_collab_predictions': batch_kind == 'review',
                'label_config': config, 'show_instruction': True,
                'expert_instruction': description + ' Use docs/annotation_codebook.md. Measure active time.'})
        project_id = project['id']
        current = request('GET', f'/api/projects/{project_id}/')
        if current.get('label_config') != config:
            if current.get('num_tasks_with_annotations', 0):
                raise ValueError('Do not replace config after annotations have started')
            request('PATCH', f'/api/projects/{project_id}/', {'label_config': config})
        if dataset == 'rdd':
            storages = request('GET', f'/api/storages/localfiles?project={project_id}')
            media = str((ROOT / 'annotation/pilot/local/images').resolve())
            if not any(s.get('path', '').replace('\\', '/') == media.replace('\\', '/') for s in storages):
                request('POST', '/api/storages/localfiles/', {'project': project_id,
                    'title': 'Pilot images', 'path': media, 'use_blob_urls': True,
                    'recursive_scan': False, 'regex_filter': '.*\\.jpg$'})
        tasks = prepared[dataset]
        count = current.get('task_number', 0)
        if count == 0:
            request('POST', f'/api/projects/{project_id}/import', tasks)
        elif count != len(tasks):
            raise ValueError(f'Existing project has {count} tasks; expected {len(tasks)}. Preserved.')
        checked = request('GET', f'/api/projects/{project_id}/')
        if checked.get('task_number') != len(tasks):
            raise ValueError(f'Unexpected imported task count: {checked.get("task_number")}')
        report['projects'].append({'id': project_id, 'title': title,
            'tasks': checked['task_number'], 'url': base + f'/projects/{project_id}/data',
            'annotated_tasks': checked.get('num_tasks_with_annotations', 0)})
    (directory / ('projects.json' if batch_kind == 'baseline' else batch_kind + '_projects.json')).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotator', choices=['A', 'B'])
    parser.add_argument('--initialize-user', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--november', dest='batch_kind', action='store_const', const='november')
    modes.add_argument('--blind', dest='batch_kind', action='store_const', const='blind')
    modes.add_argument('--review', dest='batch_kind', action='store_const', const='review')
    parser.set_defaults(batch_kind='baseline')
    args = parser.parse_args()
    if args.initialize_user:
        initialize_user(args.annotator)
        print('Local account initialized. Credentials are in .annotation-state/' + args.annotator + '/access.json')
    else:
        configure(args.annotator, args.batch_kind)
