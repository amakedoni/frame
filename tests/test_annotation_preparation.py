"""Checks use synthetic fixtures and do not create or count human annotations."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_annotation_pilot import prepare, select_comments, select_images
from prepare_image_candidates import extract_member


class FakeRemote(io.BytesIO):
    def range_response(self, start, end):
        return io.BytesIO(self.getvalue()[start:end + 1])


class PreparationTests(unittest.TestCase):
    def test_comment_selection_deduplicates_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/raw').mkdir(parents=True)
            with zipfile.ZipFile(root / 'data/raw/youtube_spam.zip', 'w') as archive:
                for video in range(5):
                    stream = io.StringIO()
                    writer = csv.writer(stream)
                    writer.writerow(['CONTENT', 'CLASS'])
                    for label in range(2):
                        for index in range(7):
                            writer.writerow([f'fixture {video} {label} {index}', label])
                    writer.writerow(['fixture 0 0 0', 0])
                    archive.writestr(f'video{video}.csv', stream.getvalue())
            first = select_comments(root, 42)
            self.assertEqual(first, select_comments(root, 42))
            self.assertEqual(len(first), 50)
            self.assertEqual(len({r['text_sha256'] for r in first}), 50)
            self.assertEqual(sum(r['original_label'] == 'spam' for r in first), 25)

    def test_image_selection_rejects_leaks_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / 'data/processed'
            processed.mkdir(parents=True)
            columns = ['image_id', 'classes', 'pixel_sha256', 'all_boxes_valid',
                       'xml_size_matches', 'also_in_official_test']
            with (processed / 'rdd_us_images.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, columns)
                writer.writeheader()
                for label in ['D00', 'D10', 'D20', 'D40']:
                    for index in range(20):
                        writer.writerow(dict(zip(columns, [f'{label}_{index}', label,
                            f'{label}-pixel-{index}', True, True, index == 0])))
                writer.writerow(dict(zip(columns, ['duplicate', 'D40',
                    'D40-pixel-1', True, True, False])))
            (processed / 'rdd_near_duplicate_candidates.csv').write_text(
                'image_a,image_b,dhash_distance\nD40_2,D40_3,1\n', encoding='utf8')
            chosen = select_images(root, 42)
            self.assertEqual(chosen, select_images(root, 42))
            self.assertEqual(len(chosen), 50)
            self.assertEqual(len({r['pixel_sha256'] for r in chosen}), 50)
            ids = {r['image_id'] for r in chosen}
            self.assertFalse(ids & {'D00_0', 'D10_0', 'D20_0', 'D40_0', 'D40_2', 'D40_3', 'duplicate'})

    def test_existing_batch_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'local'
            output.mkdir()
            marker = output / 'answers.json'
            marker.write_text('preserve', encoding='utf8')
            with self.assertRaises(FileExistsError):
                prepare(root=Path(directory), output=output)
            self.assertEqual(marker.read_text(encoding='utf8'), 'preserve')

    def test_bounded_zip_extraction_checks_crc(self):
        packed = io.BytesIO()
        content = b'fixture data' * 100
        with zipfile.ZipFile(packed, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('images/example.jpg', content)
        with zipfile.ZipFile(io.BytesIO(packed.getvalue())) as archive:
            info = archive.getinfo('images/example.jpg')
        self.assertEqual(extract_member(FakeRemote(packed.getvalue()), info), content)
        info.CRC ^= 1
        with self.assertRaises(ValueError):
            extract_member(FakeRemote(packed.getvalue()), info)

    def test_prepared_real_tasks_are_blinded(self):
        pilot = Path(__file__).resolve().parents[1] / 'annotation/pilot/local'
        if not pilot.exists():
            self.skipTest('Local pilot not prepared')
        for filename, field in [('youtube_tasks.json', 'text'), ('rdd_tasks.json', 'image')]:
            tasks = json.loads((pilot / filename).read_text(encoding='utf8'))
            self.assertEqual(len(tasks), 50)
            self.assertEqual(len({t['data']['item_id'] for t in tasks}), 50)
            for task in tasks:
                self.assertEqual(set(task), {'data'})
                self.assertEqual(set(task['data']), {'item_id', field})
        summary = json.loads((pilot / 'summary.json').read_text(encoding='utf8'))
        self.assertEqual(summary['annotations_created'], 0)


if __name__ == '__main__':
    unittest.main()
