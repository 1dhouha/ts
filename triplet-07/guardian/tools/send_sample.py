"""Send a partner's sample message to Guardian and print the answer.
Usage:  python tools/send_sample.py <partner-profile> <file>        e.g.  python tools/send_sample.py default twin_sample.json
Options: --url http://localhost:8002   --token <bearer token>"""
import argparse
import json
import sys

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("partner")
ap.add_argument("file")
ap.add_argument("--url", default="http://localhost:8002")
ap.add_argument("--token", default=None)
a = ap.parse_args()
headers = {"Authorization": f"Bearer {a.token}"} if a.token else {}
body = open(a.file, "rb").read()
r = httpx.post(f"{a.url}/ingest/{a.partner}", content=body, headers=headers, timeout=5)
print("HTTP", r.status_code)
try:
    print(json.dumps(r.json(), indent=2))
except ValueError:
    print(r.text)
sys.exit(0 if r.status_code == 200 else 1)
