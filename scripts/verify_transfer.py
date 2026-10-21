"""Verify a transferred project against its SHA-256 manifest using only stdlib."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = args.manifest or root / 'TRANSFER_MANIFEST.json'
    if not manifest.exists():
        print('No transfer manifest found. Use this check inside the extracted full transfer package.')
        return 1
    data = json.loads(manifest.read_text(encoding='utf-8'))
    failures = []
    for entry in data['files']:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root.resolve()):
            failures.append((entry['path'], 'path outside project'))
            continue
        if not path.is_file():
            failures.append((entry['path'], 'missing'))
            continue
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                digest.update(block)
        if path.stat().st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            failures.append((entry['path'], 'size or SHA-256 mismatch'))
    if failures:
        for filename, reason in failures:
            print(f'FAIL {filename}: {reason}')
        return 1
    print(f"OK: {len(data['files'])} files match the transfer manifest.")
    return 0

if __name__ == '__main__':
    sys.exit(main())
