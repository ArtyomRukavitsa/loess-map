# Публикует index.html как статический сайт в Yandex Object Storage.
# Создаёт бакет, делает публичным, грузит, включает website-hosting. URL: https://<bucket>.website.yandexcloud.net
import os, sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sec = {}
for line in open(os.path.join(HERE, "..", "loess_pipeline", ".secrets"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        sec[k] = v
import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
BUCKET = os.environ.get("SITE_BUCKET", "loess-map")

# 1) создать бакет (если ещё нет)
try:
    s3.create_bucket(Bucket=BUCKET)
    print("бакет создан:", BUCKET)
except ClientError as e:
    code = e.response["Error"]["Code"]
    if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
        print("бакет уже есть:", BUCKET, f"({code})")
    else:
        print("ОШИБКА создания бакета:", code, "-", e.response["Error"].get("Message"))
        sys.exit(1)

def step(name, fn):
    try:
        fn(); print("  ✓", name); return True
    except ClientError as e:
        print("  ✗", name, "—", e.response["Error"]["Code"]); return False

# 2) загрузить index.html
step("загрузка index.html", lambda: s3.put_object(
    Bucket=BUCKET, Key="index.html", Body=open(os.path.join(HERE, "index.html"), "rb").read(),
    ContentType="text/html; charset=utf-8"))

# 3) website-hosting
web_ok = step("website-hosting", lambda: s3.put_bucket_website(Bucket=BUCKET, WebsiteConfiguration={
    "IndexDocument": {"Suffix": "index.html"}, "ErrorDocument": {"Key": "index.html"}}))

# 4) публичный доступ (может не хватить прав ключа)
acl_ok = step("публичный доступ (ACL public-read)", lambda: s3.put_bucket_acl(Bucket=BUCKET, ACL="public-read"))

print("\nURL:  https://%s.website.yandexcloud.net" % BUCKET)
if not acl_ok:
    print("\n⚠️ Публичный доступ ключом не выставился. Включи вручную в консоли Yandex Cloud:")
    print("   Object Storage → бакет 'loess-map' → вкладка 'Публичный доступ' →")
    print("   'Чтение объектов' (или 'Чтение объектов и списка') → Сохранить.")
