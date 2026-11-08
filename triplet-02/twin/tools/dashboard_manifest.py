"""Content identities for local dashboard review; no Git or dependency install."""
import argparse
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def content_bytes(path):
    data = path.read_bytes()
    if path.is_relative_to(ROOT/'docs/architecture/dashboard') and path.name.endswith('_REPORT.md'):
        return data.split(b'<!-- EXECUTION EVIDENCE:', 1)[0]
    return data


def manifest():
    paths=[]
    for directory in ('person_a/src','person_a/tests','person_a/live_tests','person_a/dashboard_tests','person_a/tools','person_a/fixtures','ready','output/pdf','docs/architecture/dashboard','docs/integration'):
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    paths.extend(ROOT/p for p in ('AGENTS.md','person_a/README.md','person_a/Start Dashboard.cmd','person_a/pyproject.toml','docs/architecture/README.md','docs/architecture/DASHBOARD_PHASED_PLAN.md','docs/architecture/ENGINEERING_QUALITY_LOOP.md','docs/challenge/PHASE1_REQUIREMENTS.md','Phase 1 City Brain Convergence.pdf','City Brain Convergence.pdf'))
    baseline=ROOT/'tmp/dashboard_remaining/baseline.json'
    if baseline.is_file():
        paths.append(baseline)
    result={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(content_bytes(p)).hexdigest() for p in sorted(set(paths))}
    raw=json.dumps(result,sort_keys=True,indent=2)
    return {'files':result,'sha256':hashlib.sha256(raw.encode()).hexdigest(),'exclusions':'pycache; rolling review log; only dashboard phase *_REPORT.md evidence suffix after EXECUTION EVIDENCE marker'}
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    result=manifest();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(result['sha256'])
