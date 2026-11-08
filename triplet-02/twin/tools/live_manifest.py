"""Bind substantive source/docs and verify original core inputs were preserved."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE = {'docs/architecture/ENGINEERING_REVIEW_LOG.md', 'docs/integration/LIVE_CITY_VERIFICATION.md'}


def manifest():
    paths = [ROOT / 'AGENTS.md', ROOT / 'City Brain Convergence.pdf', ROOT / 'Phase 1 City Brain Convergence.pdf']
    for folder in ('person_a', 'docs/architecture', 'docs/challenge', 'docs/integration'):
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and p.suffix in ('.py', '.json', '.toml', '.md', '.pdf'))
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if p.relative_to(ROOT).as_posix() not in EXCLUDE}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', type=Path)
    parser.add_argument('--check', type=Path)
    args = parser.parse_args()
    data = manifest()
    preserved = json.loads((ROOT / 'tmp/live_city/protected_baseline.json').read_text())
    assert all(data.get(path) == sha for path, sha in preserved.items()), 'original core input changed'
    payload = json.dumps(data, sort_keys=True, indent=2)
    if args.check:
        assert json.loads(args.check.read_text()) == data, 'reviewed content changed'
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(payload, encoding='utf-8')
    print(json.dumps({'inputs': len(data), 'manifest_sha256': hashlib.sha256(payload.encode()).hexdigest(),
                      'protected_original_files': len(preserved), 'passed': True}))


if __name__ == '__main__':
    main()
