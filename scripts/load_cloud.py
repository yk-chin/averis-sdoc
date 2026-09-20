"""Load the organiser's inbox into the Cloud Run service (Firestore) through POST /batch.

    python scripts/load_cloud.py ./data --api https://shipdoc-api-....run.app [--batch-size 200]

Reads data/inbox/*.json and data/attachments/* only (never ground_truth.json). The API key is read from
Secret Manager into memory via gcloud and is never printed or written. Emails already DONE on the service
are reported as duplicates by the API and not re-processed. Ends by retrying every dead-lettered email once
and printing the final counts. Nothing here touches the repo or submission.json.
"""
import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

GCLOUD = os.environ.get("GCLOUD", r"C:\Users\Admin\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd")
SECRET = "shipdoc-api-token"


def api_key() -> str:
    if os.environ.get("API_TOKEN"):
        return os.environ["API_TOKEN"]
    out = subprocess.run([GCLOUD, "secrets", "versions", "access", "latest", "--secret", SECRET],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def call(url: str, key: str, payload=None, method="GET") -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-API-Key": key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code >= 500 and attempt < 3:
                time.sleep(3 * (attempt + 1)); continue
            raise SystemExit(f"HTTP {e.code} on {method} {url}: {body}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(3 * (attempt + 1)); continue
            raise SystemExit(f"network error on {method} {url}: {e}")


def load_emails(data: pathlib.Path) -> list[dict]:
    inbox, att = data / "inbox", data / "attachments"
    emails = []
    for f in sorted(inbox.glob("*.json")):
        e = json.loads(f.read_text(encoding="utf-8"))
        atts = []
        for rel in e.get("attachments") or []:          # inbox references are "attachments/<file>"
            p = data / rel if (data / rel).exists() else att / pathlib.Path(rel).name
            if p.exists():
                atts.append({"name": p.name, "content_base64": base64.b64encode(p.read_bytes()).decode()})
            # referenced but missing: omitted, exactly as pipeline.run.classify_attachments skips it locally
        emails.append({"email_id": e["email_id"], "from": e.get("from", ""), "subject": e.get("subject", ""),
                       "body": e.get("body", ""), "attachments": atts})
    return emails


def wait_batch(api: str, key: str, batch_id: str) -> dict:
    last = None
    while True:
        s = call(f"{api}/batch/{batch_id}", key)
        line = f"  {batch_id}: done {s['done']} failed {s['failed']} pending {s['pending']}"
        if line != last:
            print(line, flush=True); last = line
        if s["pending"] == 0:
            return s
        time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("--api", required=True); ap.add_argument("--batch-size", type=int, default=200)
    a = ap.parse_args()
    api = a.api.rstrip("/")
    key = api_key()
    emails = load_emails(pathlib.Path(a.data))
    print(f"{len(emails)} emails, {sum(len(e['attachments']) for e in emails)} attachments")

    for i in range(0, len(emails), a.batch_size):
        chunk = emails[i:i + a.batch_size]
        r = call(f"{api}/batch", key, {"emails": chunk}, "POST")
        print(f"batch {r['batch_id']}: queued {r['queued']} duplicates {r['duplicates']}", flush=True)
        if r["queued"]:
            wait_batch(api, key, r["batch_id"])

    failed = call(f"{api}/failures?limit=600", key)["items"]
    if failed:
        print(f"{len(failed)} dead-lettered; retrying once", flush=True)
        for it in failed:
            call(f"{api}/failures/{it['id']}/retry", key, {}, "POST")
        time.sleep(20)
        deadline = time.time() + 600
        while time.time() < deadline:
            still = call(f"{api}/failures?limit=600", key)["items"]
            if not still:
                break
            print(f"  still failed: {len(still)}", flush=True); time.sleep(10)

    rows = call(f"{api}/reports?limit=1000&prefix=email_", key)["items"]
    cats, stats = {}, {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
        stats[r["status"]] = stats.get(r["status"], 0) + 1
    print(f"reports with prefix email_: {len(rows)}")
    print("by category:", json.dumps(cats, sort_keys=True))
    print("by status:  ", json.dumps(stats, sort_keys=True))
    print("still FAILED:", len(call(f"{api}/failures?limit=600", key)["items"]))


if __name__ == "__main__":
    sys.exit(main())
