"""Emit actual private checkpoint evidence, plus an optional separate safe export."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from city_live.demo import demonstrate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    result = demonstrate(lambda row: print(json.dumps(row)))
    safe = result.pop('safe_export')
    (args.evidence / 'observations_public.json').write_text(json.dumps(safe, indent=2), encoding='utf-8')
    (args.evidence / 'demo_private.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
