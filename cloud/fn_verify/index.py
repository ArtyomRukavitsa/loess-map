# Cloud Function: приём и выдача ручных проверок данных по разрезам (верификация коллегами).
# Хранение — JSON на разрез в Object Storage (corrections/<section_id>.json). Публичный HTTP.
# ENV: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BUCKET (как у fn_extract_text).
# POST {section_id, field, verdict, comment, user} -> добавить правку
# GET  ?section_id=... -> вернуть все правки по разрезу
import os, json, time, base64
import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
BUCKET = os.environ.get("BUCKET", "loess-results")
PREFIX = "corrections/"
VERDICTS = {"correct", "partial", "incorrect"}

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

def _resp(code, body):
    return {"statusCode": code, "headers": {**CORS, "Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}

def _key(sid):
    safe = "".join(c for c in str(sid) if c.isalnum() or c in "-_.")[:80] or "unknown"
    return PREFIX + safe + ".json"

def _read(sid):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=_key(sid))["Body"].read())
    except ClientError:
        return []
    except Exception:
        return []

def handler(event, context):
    method = str(event.get("httpMethod") or "GET").upper()
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if method == "GET":
        sid = (event.get("queryStringParameters") or {}).get("section_id", "")
        if not sid:
            return _resp(400, {"error": "section_id required"})
        return _resp(200, {"section_id": sid, "corrections": _read(sid)})

    if method == "POST":
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            try: raw = base64.b64decode(raw).decode("utf-8")
            except Exception: pass
        try:
            d = json.loads(raw)
        except Exception:
            return _resp(400, {"error": "invalid json body"})
        sid = d.get("section_id")
        if not sid:
            return _resp(400, {"error": "section_id required"})
        verdict = str(d.get("verdict", "")).lower()
        if verdict not in VERDICTS:
            return _resp(400, {"error": "verdict must be correct/partial/incorrect"})
        rec = {"field": str(d.get("field", "overall"))[:60], "verdict": verdict,
               "comment": str(d.get("comment", ""))[:1000], "user": str(d.get("user", "anon"))[:60],
               "ts": int(time.time())}
        cur = _read(sid)
        cur.append(rec)
        s3.put_object(Bucket=BUCKET, Key=_key(sid),
                      Body=json.dumps(cur, ensure_ascii=False).encode("utf-8"),
                      ContentType="application/json")
        return _resp(200, {"ok": True, "count": len(cur), "saved": rec})

    return _resp(405, {"error": "method not allowed"})
