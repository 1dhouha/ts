"""Read-only baseline/link protection for the authorized local dashboard scope."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote
ROOT=Path(__file__).resolve().parents[2]

def check(baseline):
    saved=json.loads(baseline.read_text(encoding='utf-8'))
    allowed=[];preserved=[];bad=[]
    for name,digest in saved.items():
        path=ROOT/name
        current=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if current==digest:
            preserved.append(name)
        elif name.startswith('person_a/src/city_dashboard/') or name in ('person_a/src/city_live/runtime.py','person_a/src/city_live/operator.py'):
            allowed.append(name)
        else:
            bad.append(name)
    links=0;broken=[]
    paths=[ROOT/p for p in ('docs/architecture/dashboard/README.md','docs/architecture/README.md','person_a/README.md','docs/architecture/DASHBOARD_PHASED_PLAN.md')]
    paths.extend((ROOT/'docs/architecture/dashboard').glob('d0[3-7]/*_REPORT.md'))
    for path in paths:
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
            if target.startswith(('http:','https:','mailto:','#')):
                continue
            target=unquote(target.split('#')[0]).strip('<>')
            if not target:
                continue
            links+=1
            if not (path.parent/target).exists():
                broken.append([str(path.relative_to(ROOT)),target])
    original=json.loads((ROOT/'docs/architecture/dashboard/d01/map-layout.json').read_text(encoding='utf-8'))
    served=json.loads((ROOT/'person_a/src/city_dashboard/web/map-layout.json').read_text(encoding='utf-8'))
    geometry_ok=all(original[key]==served[key] for key in ('viewBox','anchors','districts','roads','devices'))
    return {'passed':not bad and not broken and geometry_ok and not (ROOT/'.git').exists(),
        'served_geometry_matches_accepted_design':geometry_ok,
        'preserved_baseline_files':len(preserved),'permitted_changed_baseline_files':allowed,
        'unexpected_changed_files':bad,'checked_links':links,'broken_links':broken,
        'git_repository_created':(ROOT/'.git').exists(),
        'phase1_pdf_sha256':hashlib.sha256((ROOT/'Phase 1 City Brain Convergence.pdf').read_bytes()).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',type=Path,default=ROOT/'tmp/dashboard_remaining/baseline.json');parser.add_argument('--evidence',type=Path,required=True);args=parser.parse_args()
    result=check(args.baseline);args.evidence.parent.mkdir(parents=True,exist_ok=True);args.evidence.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
