"""Prepare blinded Label Studio tasks from existing data, never human answers."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
import tempfile
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SEED = 42
IMAGE_TARGETS = {'D40': 13, 'D20': 13, 'D10': 12, 'D00': 12}


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def select_comments(root: Path, seed: int) -> list[dict]:
    """Five examples per original class per video; class never reaches tasks."""
    rng = random.Random(seed)
    groups = defaultdict(list)
    seen = set()
    with zipfile.ZipFile(root / 'data/raw/youtube_spam.zip') as archive:
        members = sorted(n for n in archive.namelist()
                         if n.lower().endswith('.csv') and not n.startswith('__MACOSX'))
        for member in members:
            stream = io.StringIO(archive.read(member).decode('utf-8-sig'))
            for index, row in enumerate(csv.DictReader(stream)):
                text = row['CONTENT'].strip()
                label = row['CLASS'].strip()
                if not text or label not in {'0', '1'} or text in seen:
                    continue
                seen.add(text)
                groups[(member, label)].append({
                    'text': text, 'text_sha256': sha256(text.encode('utf-8')),
                    'source_member': member, 'source_row': index,
                    'original_label': {'0': 'ham', '1': 'spam'}[label],
                })
    if len(groups) != 10:
        raise ValueError(f'Expected five videos and two classes, found {len(groups)} groups')
    chosen = []
    for key in sorted(groups):
        if len(groups[key]) < 5:
            raise ValueError(f'Not enough comments in source group {key}')
        chosen.extend(rng.sample(groups[key], 5))
    rng.shuffle(chosen)
    return chosen


def select_images(root: Path, seed: int) -> list[dict]:
    """Rare classes first; reject exact/test duplicates and near-pair candidates."""
    rng = random.Random(seed)
    near_ids = set()
    for pair in read_csv(root / 'data/processed/rdd_near_duplicate_candidates.csv'):
        near_ids.update([pair['image_a'], pair['image_b']])
    seen_pixels = set()
    eligible = []
    for row in read_csv(root / 'data/processed/rdd_us_images.csv'):
        if (row['all_boxes_valid'] != 'True' or row['xml_size_matches'] != 'True'
                or row['also_in_official_test'] == 'True' or row['image_id'] in near_ids
                or row['pixel_sha256'] in seen_pixels):
            continue
        seen_pixels.add(row['pixel_sha256'])
        eligible.append(row)
    eligible.sort(key=lambda r: r['image_id'])
    selected, selected_ids = [], set()
    for label, count in IMAGE_TARGETS.items():
        pool = [row for row in eligible
                if label in row['classes'].split('|') and row['image_id'] not in selected_ids]
        if len(pool) < count:
            raise ValueError(f'Not enough eligible images for {label}: {len(pool)} < {count}')
        for original in rng.sample(pool, count):
            row = dict(original, selection_class=label)
            selected.append(row)
            selected_ids.add(row['image_id'])
    rng.shuffle(selected)
    return selected


def prepare(root: Path = ROOT, output: Path | None = None, seed: int = SEED) -> dict:
    output = output or root / 'annotation/pilot/local'
    if output.exists():
        raise FileExistsError(f'{output} already exists. Existing tasks and answers are preserved.')
    comments = select_comments(root, seed)
    images = select_images(root, seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.pilot-', dir=output.parent))
    try:
        (staging / 'images').mkdir()
        tasks_text, tasks_images, manifest, reference = [], [], [], []
        for index, row in enumerate(comments, 1):
            item_id = f'TXT-P{index:03}'
            tasks_text.append({'data': {'item_id': item_id, 'text': row['text']}})
            manifest.append({'item_id': item_id, 'dataset': 'youtube',
                             'origin': 'baseline_pilot', 'content_sha256': row['text_sha256']})
            reference.append(dict(row, item_id=item_id, dataset='youtube'))
        with zipfile.ZipFile(root / 'data/raw/RDD2022_United_States.zip') as archive:
            for index, row in enumerate(images, 1):
                item_id = f'IMG-P{index:03}'
                content = archive.read(row['zip_member'])
                if sha256(content) != row['sha256']:
                    raise ValueError(f'Image hash differs from EDA: {row["image_id"]}')
                with Image.open(io.BytesIO(content)) as image:
                    image.load()
                    if image.size != (int(row['width']), int(row['height'])):
                        raise ValueError('Image dimensions differ from EDA')
                    if sha256(image.convert('RGB').tobytes()) != row['pixel_sha256']:
                        raise ValueError('Image pixels differ from EDA')
                (staging / 'images' / f'{item_id}.jpg').write_bytes(content)
                tasks_images.append({'data': {'item_id': item_id,
                    'image': f'/data/local-files/?d=images/{item_id}.jpg'}})
                manifest.append({'item_id': item_id, 'dataset': 'rdd', 'origin': 'baseline_pilot',
                    'content_sha256': row['sha256'], 'pixel_sha256': row['pixel_sha256'],
                    'width': int(row['width']), 'height': int(row['height'])})
                reference.append(dict(row, item_id=item_id, dataset='rdd'))
        codebook = root / 'docs/annotation_codebook.md'
        summary = {
            'batch': 'baseline_pilot_v1', 'seed': seed,
            'codebook_version': '0.1', 'codebook_sha256': sha256(codebook.read_bytes()),
            'youtube_tasks': len(tasks_text), 'rdd_tasks': len(tasks_images),
            'youtube_source_class_counts': dict(Counter(r['original_label'] for r in comments)),
            'rdd_selection_class_targets': IMAGE_TARGETS,
            'source': 'Existing UCI and RDD2022 United States train, not new collected data',
            'annotations_created': 0,
            'rdd_source': 'https://doi.org/10.6084/m9.figshare.21431547',
            'youtube_source': 'https://doi.org/10.24432/C58885',
            'source_license': 'CC BY 4.0; credit original dataset authors',
        }
        write_json(staging / 'youtube_tasks.json', tasks_text)
        write_json(staging / 'rdd_tasks.json', tasks_images)
        write_json(staging / 'manifest.json', manifest)
        write_json(staging / 'summary.json', summary)
        # Never serve the source-label mapping as part of the Label Studio media root.
        reference_path = output.parent / 'private_reference' / (output.name + '.json')
        if reference_path.exists():
            raise FileExistsError(f'Reference mapping already exists: {reference_path}')
        write_json(reference_path, reference)
        staging.rename(output)
        return summary
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New output directory; existing directories are refused')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(output=args.output), ensure_ascii=False, indent=2))
    except (FileExistsError, ValueError, FileNotFoundError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
