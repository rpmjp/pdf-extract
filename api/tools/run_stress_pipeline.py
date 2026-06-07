import argparse
import json
import mimetypes
import time
import uuid
from pathlib import Path
from urllib import error, request


def request_json(method: str, url: str, *, token: str | None = None, data=None, headers=None):
    body = None
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:
            payload = {"detail": str(exc)}
        return exc.code, payload


def multipart_upload(url: str, path: Path, token: str):
    boundary = f"----pdfextract{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/pdf"
    chunks = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(chunks)
    req = request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:
            payload = {"detail": str(exc)}
        return exc.code, payload


def login(api_url: str) -> str:
    status, body = request_json(
        "POST",
        f"{api_url}/auth/login",
        data={"username": "reviewer", "password": "review123"},
    )
    if status != 200:
        raise RuntimeError(f"login failed: {status} {body}")
    return body["access_token"]


def extract_duplicate_id(detail: str) -> int | None:
    if "id=" not in detail:
        return None
    raw = detail.split("id=")[-1].rstrip(")")
    return int(raw) if raw.isdigit() else None


def terminal(status: str) -> bool:
    return status in {"success", "failed"}


def compare(case: dict, detail: dict | None, job: dict | None) -> dict:
    failures = []
    if not detail:
        failures.append("missing document detail")
        return {"passed": False, "failures": failures}

    expected_status = case["expected_status"]
    actual_status = detail.get("status")
    if expected_status == "verified" and actual_status not in {"verified", "approved"}:
        failures.append(f"status expected verified, got {actual_status}")
    if expected_status == "needs_review" and actual_status != "needs_review":
        failures.append(f"status expected needs_review, got {actual_status}")

    txns = detail.get("transactions") or []
    if len(txns) != case["expected_txn_count"]:
        failures.append(f"txn count expected {case['expected_txn_count']}, got {len(txns)}")

    closing = detail.get("closing_balance")
    expected_closing = case.get("expected_closing")
    if expected_closing is not None:
        if closing is None:
            failures.append(f"closing expected {expected_closing}, got None")
        elif abs(float(closing) - float(expected_closing)) >= 0.02:
            failures.append(f"closing expected {expected_closing}, got {closing}")

    confidence = detail.get("confidence_score")
    if confidence is None:
        failures.append("missing confidence_score")

    if job and job.get("status") != "success":
        failures.append(f"job status {job.get('status')}")

    return {"passed": not failures, "failures": failures}


def fetch_stable_detail(api_url: str, token: str, doc_id: int, job: dict | None, attempts: int = 6) -> dict:
    detail = {}
    for attempt in range(attempts):
        _, detail = request_json("GET", f"{api_url}/documents/{doc_id}", token=token)
        if job and job.get("status") == "success" and detail.get("status") in {"queued", "parsing"}:
            time.sleep(1)
            continue
        break
    return detail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--poll", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    cases = json.loads(manifest_path.read_text())
    if args.limit:
        cases = cases[: args.limit]

    token = login(args.api_url)
    report = {
        "manifest": str(manifest_path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": [],
    }

    print(f"Uploading and queueing {len(cases)} cases...", flush=True)
    for case in cases:
        path = Path(case["file"])
        entry = {**case, "upload": None, "job": None, "detail": None, "comparison": None}
        status, body = multipart_upload(f"{args.api_url}/documents", path, token)
        if status == 409:
            doc_id = extract_duplicate_id(str(body.get("detail", "")))
            entry["upload"] = {"status": status, "duplicate_id": doc_id, "body": body}
        elif status == 200:
            doc_id = body["id"]
            entry["upload"] = {"status": status, "document_id": doc_id}
        else:
            entry["upload"] = {"status": status, "body": body}
            report["cases"].append(entry)
            print(f"UPLOAD FAIL {case['slug']}: {status} {body}", flush=True)
            continue

        parse_status, parse_body = request_json("POST", f"{args.api_url}/documents/{doc_id}/parse", token=token)
        entry["document_id"] = doc_id
        entry["parse"] = {"status": parse_status, "body": parse_body}
        if "job_id" not in parse_body:
            print(f"PARSE ENQUEUE FAIL {case['slug']}: {parse_status} {parse_body}", flush=True)
        else:
            print(f"QUEUED {case['slug']} doc={doc_id} job={parse_body['job_id']}", flush=True)
        report["cases"].append(entry)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        pending = 0
        for entry in report["cases"]:
            job_id = (entry.get("parse") or {}).get("body", {}).get("job_id")
            if not job_id:
                continue
            current = entry.get("job")
            if current and terminal(current.get("status", "")):
                continue
            _, job_body = request_json("GET", f"{args.api_url}/jobs/{job_id}", token=token)
            entry["job"] = job_body
            if not terminal(job_body.get("status", "")):
                pending += 1
        print(f"POLL pending={pending}", flush=True)
        if pending == 0:
            break
        time.sleep(args.poll)

    for entry in report["cases"]:
        doc_id = entry.get("document_id")
        if doc_id:
            entry["detail"] = fetch_stable_detail(args.api_url, token, doc_id, entry.get("job"))
        entry["comparison"] = compare(entry, entry.get("detail"), entry.get("job"))

    passed = sum(1 for entry in report["cases"] if entry["comparison"]["passed"])
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["summary"] = {
        "total": len(report["cases"]),
        "passed": passed,
        "failed": len(report["cases"]) - passed,
    }
    out = manifest_path.parent / "stress_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
