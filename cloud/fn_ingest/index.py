# Cloud Function: fn_ingest — загрузка новых публикаций пользователем и приём решений по предложенным разрезам.
# Файл льётся В ОБХОД функции (presigned PUT прямо в Object Storage) — нет лимита на размер тела запроса.
# Обработку (OCR -> extract -> геокод) делает 57_process_uploads.py, он же пишет proposals.json.
# Раскладка в бакете:
#   uploads/<job_id>/source.<ext>   — исходный файл
#   uploads/<job_id>/status.json    — {job_id, filename, status, created, msg, n_proposals}
#   uploads/<job_id>/proposals.json — предложенные разрезы (пишет обработчик)
#   uploads/<job_id>/decisions.json — решения проверяющего {idx: {verdict, comment, user, ts}}
# ENV: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BUCKET
import os, json, time, base64, uuid
import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
BUCKET = os.environ.get("BUCKET", "loess-results")
PREFIX = "uploads/"
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".djvu"}
VERDICTS = {"accept", "reject"}

CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}

def _resp(code, body):
    return {"statusCode": code, "headers": {**CORS, "Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}

def _get(key, default=None):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        return default

def _put(key, obj):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  ContentType="application/json")

def _safe_job(jid):
    return "".join(c for c in str(jid) if c.isalnum() or c in "-_")[:40]

def handler(event, context):
    method = str(event.get("httpMethod") or "GET").upper()
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if method == "GET":
        q = event.get("queryStringParameters") or {}
        action = q.get("action", "jobs")
        if action == "jobs":                       # список задач (последние 50)
            items = []
            tok = None
            while True:
                kw = {"Bucket": BUCKET, "Prefix": PREFIX, "MaxKeys": 1000}
                if tok: kw["ContinuationToken"] = tok
                r = s3.list_objects_v2(**kw)
                items += [(o["Key"], o["LastModified"]) for o in r.get("Contents", [])
                          if o["Key"].endswith("/status.json")]
                tok = r.get("NextContinuationToken")
                if not r.get("IsTruncated"): break
            items.sort(key=lambda x: x[1], reverse=True)
            jobs = [j for j in (_get(k) for k, _ in items[:50]) if j]
            return _resp(200, {"jobs": jobs})
        if action == "job":                        # статус + предложения + решения одной задачи
            jid = _safe_job(q.get("job_id", ""))
            if not jid: return _resp(400, {"error": "job_id required"})
            base = PREFIX + jid + "/"
            return _resp(200, {"status": _get(base + "status.json", {}),
                               "proposals": _get(base + "proposals.json", []),
                               "decisions": _get(base + "decisions.json", {})})
        return _resp(400, {"error": "unknown action"})

    if method == "POST":
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            try: raw = base64.b64decode(raw).decode("utf-8")
            except Exception: pass
        try:
            d = json.loads(raw)
        except Exception:
            return _resp(400, {"error": "invalid json body"})
        action = d.get("action", "")

        if action == "create":                     # выдать ссылку для прямой заливки файла
            fname = str(d.get("filename", "upload.pdf"))[:150]
            ext = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ""
            if ext not in ALLOWED_EXT:
                return _resp(400, {"error": f"формат {ext or '?'} не поддержан; нужен PDF или изображение"})
            jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            key = f"{PREFIX}{jid}/source{ext}"
            # тип фиксируем жёстко: браузер обязан прислать РОВНО его, иначе подпись не сойдётся
            url = s3.generate_presigned_url("put_object",
                                            Params={"Bucket": BUCKET, "Key": key,
                                                    "ContentType": "application/octet-stream"},
                                            ExpiresIn=3600)
            _put(f"{PREFIX}{jid}/status.json",
                 {"job_id": jid, "filename": fname, "status": "created", "created": int(time.time()),
                  "msg": "ожидается загрузка файла", "n_proposals": 0,
                  "user": str(d.get("user", "anon"))[:60]})
            return _resp(200, {"job_id": jid, "upload_url": url, "key": key,
                               "content_type": "application/octet-stream"})

        if action == "enqueue":                    # файл залит -> в очередь на обработку
            jid = _safe_job(d.get("job_id", ""))
            if not jid: return _resp(400, {"error": "job_id required"})
            k = f"{PREFIX}{jid}/status.json"
            st = _get(k)
            if not st: return _resp(404, {"error": "job не найден"})
            st.update({"status": "queued", "msg": "в очереди на обработку", "queued": int(time.time())})
            _put(k, st)
            return _resp(200, {"ok": True, "status": st})

        if action == "decide":                     # решение проверяющего по предложенному разрезу
            jid = _safe_job(d.get("job_id", ""))
            verdict = str(d.get("verdict", "")).lower()
            if not jid: return _resp(400, {"error": "job_id required"})
            if verdict not in VERDICTS:
                return _resp(400, {"error": "verdict must be accept/reject"})
            try:
                i = str(int(d.get("i")))
            except Exception:
                return _resp(400, {"error": "i (индекс предложения) required"})
            k = f"{PREFIX}{jid}/decisions.json"
            dec = _get(k, {}) or {}
            dec[i] = {"verdict": verdict, "comment": str(d.get("comment", ""))[:1000],
                      "user": str(d.get("user", "anon"))[:60], "ts": int(time.time())}
            _put(k, dec)
            return _resp(200, {"ok": True, "decided": len(dec)})

        return _resp(400, {"error": "unknown action"})

    return _resp(405, {"error": "method not allowed"})
