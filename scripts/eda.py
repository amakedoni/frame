"""Reproducible initial EDA; no enrichment, annotation or model training."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import io
import json
import time
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
PROCESSED = ROOT / 'data' / 'processed'
REPORTS = ROOT / 'reports'
FIGURES = REPORTS / 'figures'
SEED = 42
CLASSES = ['D00', 'D10', 'D20', 'D40']
COLORS = {'ham': '#4279A3', 'spam': '#CE7E50', 'D00': '#4279A3', 'D10': '#67A391', 'D20': '#CE9B50', 'D40': '#BE6476'}
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'figure.dpi': 120, 'savefig.dpi': 150})

def write_csv(data, filename):
    data.to_csv(PROCESSED / filename, index=False, encoding='utf-8-sig')

def save_figure(name):
    plt.tight_layout()
    path = FIGURES / name
    plt.savefig(path, bbox_inches='tight')
    plt.close()
    return path

def youtube_eda():
    with zipfile.ZipFile(RAW / 'youtube_spam.zip') as archive:
        frames = []
        for filename in sorted(archive.namelist()):
            if filename.lower().endswith('.csv') and not filename.startswith('__MACOSX'):
                frame = pd.read_csv(io.BytesIO(archive.read(filename)))
                frame['source_file'] = Path(filename).name
                frame['source_row'] = np.arange(len(frame))
                frames.append(frame)
    data = pd.concat(frames, ignore_index=True)
    data['CONTENT'] = data['CONTENT'].astype('string')
    data['text'] = data['CONTENT'].str.strip()
    data['label'] = data['CLASS'].map({0: 'ham', 1: 'spam'})
    data['char_count'] = data['text'].str.len()
    data['word_count'] = data['text'].str.split().str.len()
    data['has_url'] = data['text'].str.contains(r'https?://|www\.', case=False, regex=True, na=False)
    data['has_html'] = data['text'].str.contains(r'<[^>]+>', regex=True, na=False)
    data['text_sha256'] = data['text'].fillna('').map(lambda x: hashlib.sha256(x.encode()).hexdigest())
    conflicts = data.groupby('text', dropna=False)['CLASS'].nunique()
    valid = data['text'].notna() & data['text'].ne('') & data['label'].notna()
    clean = data.loc[valid & ~data['text'].isin(conflicts[conflicts > 1].index)].drop_duplicates('text').copy()
    clean.to_csv(PROCESSED / 'youtube_clean.csv', index=False, encoding='utf-8-sig')
    profile_columns = ['source_file', 'source_row', 'CLASS', 'label', 'char_count', 'word_count', 'has_url', 'has_html', 'text_sha256']
    write_csv(data[profile_columns], 'youtube_profile.csv')
    counts = pd.DataFrame({'raw': data['label'].value_counts(), 'unique_clean': clean['label'].value_counts()}).reindex(['ham', 'spam'])
    write_csv(counts.reset_index(names='class'), 'youtube_class_counts.csv')
    by_video = data.groupby(['source_file', 'label']).size().unstack(fill_value=0)
    write_csv(by_video.reset_index(), 'youtube_counts_by_video.csv')
    length_summary = clean.groupby('label')[['char_count', 'word_count']].agg(['min', 'median', 'mean', 'max']).round(2)
    length_summary.columns = ['_'.join(x) for x in length_summary.columns]
    write_csv(length_summary.reset_index(), 'youtube_length_summary.csv')
    # A provisional balanced manifest, not a train/test split.
    sample = pd.concat([clean.loc[clean['CLASS'].eq(value)].sample(n=750, random_state=SEED) for value in [0, 1]])
    write_csv(sample[['source_file', 'source_row', 'CLASS', 'text_sha256']], 'youtube_working_manifest_1500.csv')
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    counts.plot.bar(ax=axes[0], color=['#A9B8C6', '#4279A3'], rot=0)
    axes[0].set(title='Комментарии до и после удаления повторов', ylabel='Число комментариев', xlabel='Класс')
    axes[0].legend(['Исходные', 'Уникальные'])
    axes[1].boxplot([clean.loc[clean['label'].eq(label), 'char_count'] for label in ['ham', 'spam']], tick_labels=['ham', 'spam'], showfliers=False)
    axes[1].set(title='Длина уникальных комментариев', ylabel='Символов без крайних пробелов')
    save_figure('youtube_balance_and_lengths.png')
    fig, ax = plt.subplots(figsize=(8, 3.5))
    by_video.rename(index=lambda x: x.replace('Youtube', '').replace('.csv', '')).plot.bar(stacked=True, ax=ax, color=[COLORS['ham'], COLORS['spam']], rot=0)
    ax.set(title='Классы по исходным видео', ylabel='Число комментариев', xlabel='Исходный файл')
    save_figure('youtube_sources.png')
    result = {
        'raw_rows': len(data), 'class_counts_raw': {k: int(v) for k, v in data['label'].value_counts().items()},
        'missing_content': int(data['text'].isna().sum()), 'empty_content_after_strip': int(data['text'].eq('').sum()),
        'unknown_labels': int(data['label'].isna().sum()), 'duplicate_text_rows_after_strip': int(data.duplicated('text').sum()),
        'conflicting_unique_texts': int((conflicts > 1).sum()), 'unique_clean_rows': len(clean),
        'class_counts_clean': {k: int(v) for k, v in clean['label'].value_counts().items()},
        'rows_with_url': int(data['has_url'].sum()), 'rows_with_html': int(data['has_html'].sum()),
        'provisional_working_rows': len(sample), 'seed': SEED,
        'median_characters_clean': {k: float(v) for k, v in clean.groupby('label')['char_count'].median().items()},
        'source_files': int(data['source_file'].nunique()),
    }
    return result, data, clean

def dhash(image):
    array = np.asarray(image.convert('L').resize((9, 8)))
    bits = (array[:, 1:] > array[:, :-1]).reshape(-1)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value

def rdd_eda():
    image_rows, box_rows, errors = [], [], []
    with zipfile.ZipFile(RAW / 'RDD2022_United_States.zip') as archive:
        names = archive.namelist()
        images = sorted(name for name in names if '/train/images/' in name and name.lower().endswith('.jpg'))
        xml_names = {Path(name).stem: name for name in names if '/train/' in name and name.endswith('.xml')}
        test_images = [name for name in names if '/test/images/' in name and name.lower().endswith('.jpg')]
        test_hashes = Counter(hashlib.sha256(archive.read(name)).hexdigest() for name in test_images)
        for i, filename in enumerate(images):
            stem = Path(filename).stem
            content = archive.read(filename)
            byte_hash = hashlib.sha256(content).hexdigest()
            try:
                image = Image.open(io.BytesIO(content)).convert('RGB')
                image.load()
                width, height = image.size
                pixel_hash = hashlib.sha256(image.tobytes()).hexdigest()
                gray = np.asarray(image.convert('L'))
                brightness = float(gray.mean())
                contrast = float(gray.std())
                perceptual_hash = dhash(image)
            except Exception as error:
                errors.append({'image_id': stem, 'type': 'unreadable_image', 'detail': str(error)})
                continue
            xml_name = xml_names.get(stem)
            if xml_name is None:
                errors.append({'image_id': stem, 'type': 'missing_xml', 'detail': ''})
                continue
            root = ET.fromstring(archive.read(xml_name))
            xml_width, xml_height = int(root.findtext('size/width')), int(root.findtext('size/height'))
            current_boxes = []
            for obj in root.findall('object'):
                label = obj.findtext('name', '').strip()
                coords = [float(obj.findtext('bndbox/' + key)) for key in ['xmin', 'ymin', 'xmax', 'ymax']]
                x1, y1, x2, y2 = coords
                valid = bool(np.isfinite(coords).all() and 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height)
                row = {'image_id': stem, 'class': label, 'xmin': x1, 'ymin': y1, 'xmax': x2, 'ymax': y2,
                       'box_valid': valid, 'class_known': label in CLASSES,
                       'area_fraction': (x2 - x1) * (y2 - y1) / (width * height),
                       'aspect_ratio': (x2 - x1) / (y2 - y1) if y2 > y1 else None}
                current_boxes.append(row)
                box_rows.append(row)
            image_rows.append({'image_id': stem, 'zip_member': filename, 'width': width, 'height': height,
                               'xml_size_matches': width == xml_width and height == xml_height,
                               'objects': len(current_boxes), 'classes': '|'.join(sorted({b['class'] for b in current_boxes})),
                               'all_boxes_valid': all(b['box_valid'] and b['class_known'] for b in current_boxes),
                               'bytes': len(content), 'sha256': byte_hash, 'pixel_sha256': pixel_hash,
                               'dhash': f'{perceptual_hash:016x}', 'brightness': round(brightness, 2),
                               'contrast': round(contrast, 2), 'also_in_official_test': byte_hash in test_hashes})
            if (i + 1) % 1000 == 0:
                print(f'RDD2022 checked {i + 1}/{len(images)} train images', flush=True)
        image_data, boxes = pd.DataFrame(image_rows), pd.DataFrame(box_rows)
        duplicate_objects = boxes.duplicated(['image_id', 'class', 'xmin', 'ymin', 'xmax', 'ymax'])
        write_csv(image_data, 'rdd_us_images.csv')
        write_csv(boxes, 'rdd_us_boxes.csv')
        write_csv(pd.DataFrame(errors, columns=['image_id', 'type', 'detail']), 'rdd_read_errors.csv')
        write_csv(boxes.loc[~boxes['box_valid'] | ~boxes['class_known'] | duplicate_objects], 'rdd_annotation_issues.csv')
        class_counts = pd.DataFrame({'objects': boxes['class'].value_counts(), 'images': boxes.groupby('class')['image_id'].nunique()}).reindex(CLASSES).fillna(0).astype(int)
        write_csv(class_counts.reset_index(names='class'), 'rdd_class_counts.csv')
        # Near-duplicate detection is only a screening heuristic, not a labeling decision.
        unique = image_data.drop_duplicates('pixel_sha256')
        hashes = [(row.image_id, int(row.dhash, 16)) for row in unique.itertuples()]
        near_pairs = []
        for index, (name, value) in enumerate(hashes):
            for other, other_hash in hashes[index + 1:]:
                distance = (value ^ other_hash).bit_count()
                if distance <= 3:
                    near_pairs.append((name, other, distance))
        write_csv(pd.DataFrame(near_pairs, columns=['image_a', 'image_b', 'dhash_distance']), 'rdd_near_duplicate_candidates.csv')
        eligible = image_data.loc[image_data['xml_size_matches'] & image_data['all_boxes_valid'] & ~image_data['also_in_official_test']].drop_duplicates('pixel_sha256')
        if len(eligible) < 3000:
            raise RuntimeError(f'Insufficient valid unique images: {len(eligible)}')
        sample = eligible.sample(3000, random_state=SEED)
        sampled_boxes = boxes.loc[boxes['image_id'].isin(sample['image_id'])]
        assert set(CLASSES).issubset(set(sampled_boxes['class']))
        write_csv(sample[['image_id', 'zip_member', 'classes', 'pixel_sha256']], 'rdd_working_manifest_3000.csv')
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
        class_counts['objects'].plot.bar(ax=axes[0], color=[COLORS[k] for k in CLASSES], rot=0)
        axes[0].set(title='Повреждения по классам', ylabel='Число рамок', xlabel='Класс')
        class_counts['images'].plot.bar(ax=axes[1], color=[COLORS[k] for k in CLASSES], rot=0)
        axes[1].set(title='Снимки с каждым классом', ylabel='Число снимков', xlabel='Один снимок может содержать несколько классов')
        save_figure('rdd_class_balance.png')
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
        axes[0].hist(image_data['objects'], bins=np.arange(0, int(image_data['objects'].max()) + 2) - .5, color='#4279A3')
        axes[0].set(title='Рамок на снимок', xlabel='Число рамок', ylabel='Снимков')
        axes[1].hist(boxes.loc[boxes['box_valid'], 'area_fraction'], bins=40, color='#67A391')
        axes[1].set(title='Размер повреждения', xlabel='Доля площади снимка', ylabel='Рамок')
        axes[2].hist(image_data['brightness'], bins=30, color='#CE9B50')
        axes[2].set(title='Средняя яркость', xlabel='Яркость от 0 до 255', ylabel='Снимков')
        save_figure('rdd_geometry_and_quality.png')
        fig, axes = plt.subplots(2, 2, figsize=(10, 10))
        for label, ax in zip(CLASSES, axes.flat):
            selected = boxes.loc[boxes['class'].eq(label) & boxes['box_valid']].sort_values('area_fraction').iloc[len(boxes.loc[boxes['class'].eq(label) & boxes['box_valid']]) // 2]
            row = image_data.loc[image_data['image_id'].eq(selected['image_id'])].iloc[0]
            ax.imshow(Image.open(io.BytesIO(archive.read(row['zip_member']))))
            for _, box in boxes.loc[boxes['image_id'].eq(row['image_id'])].iterrows():
                box_class = box['class']
                ax.add_patch(plt.Rectangle((box.xmin, box.ymin), box.xmax-box.xmin, box.ymax-box.ymin, fill=False, color=COLORS.get(box_class, '#FFFFFF'), linewidth=1.5))
                ax.text(box.xmin, max(0, box.ymin-5), box_class, color='white', fontsize=8, bbox={'facecolor': COLORS.get(box_class, '#333333'), 'alpha': .8, 'pad': 1})
            ax.set_title(label + '   ' + row['image_id'])
            ax.axis('off')
        save_figure('rdd_annotation_examples.png')
    result = {
        'scope': 'RDD2022 United States train; not the full multinational dataset',
        'train_images': len(images), 'train_xml': len(xml_names), 'official_test_images_unlabeled': len(test_images),
        'readable_paired_train_images': len(image_data), 'image_or_pair_errors': len(errors),
        'objects': len(boxes), 'class_counts_objects': {k: int(v) for k,v in class_counts['objects'].items()},
        'class_counts_images': {k: int(v) for k,v in class_counts['images'].items()},
        'images_without_objects': int(image_data['objects'].eq(0).sum()), 'invalid_boxes': int((~boxes['box_valid']).sum()),
        'unknown_class_objects': int((~boxes['class_known']).sum()), 'duplicate_annotation_rows': int(duplicate_objects.sum()),
        'xml_image_size_mismatches': int((~image_data['xml_size_matches']).sum()),
        'duplicate_file_rows': int(image_data.duplicated('sha256').sum()), 'duplicate_pixel_rows': int(image_data.duplicated('pixel_sha256').sum()),
        'exact_train_test_overlaps': int(image_data['also_in_official_test'].sum()),
        'near_duplicate_candidate_pairs_dhash_le3': len(near_pairs),
        'width_counts': {str(k): int(v) for k,v in image_data['width'].value_counts().items()},
        'height_counts': {str(k): int(v) for k,v in image_data['height'].value_counts().items()},
        'eligible_unique_images': len(eligible), 'provisional_working_images': len(sample), 'seed': SEED,
        'working_class_counts_objects': {k: int(v) for k,v in sampled_boxes['class'].value_counts().items()},
        'median_objects_per_image': float(image_data['objects'].median()),
    }
    return result, image_data, boxes

def run_all():
    started = time.perf_counter()
    for directory in [PROCESSED, REPORTS, FIGURES]:
        directory.mkdir(parents=True, exist_ok=True)
    youtube, text_data, text_clean = youtube_eda()
    rdd, image_data, boxes = rdd_eda()
    result = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'youtube': youtube, 'rdd2022_us': rdd,
              'elapsed_seconds': round(time.perf_counter() - started, 2)}
    (REPORTS / 'eda_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result

if __name__ == '__main__':
    run_all()
