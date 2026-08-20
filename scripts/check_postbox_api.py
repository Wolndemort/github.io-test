import os

import boto3


endpoint = os.environ.get("YANDEX_POSTBOX_ENDPOINT", "https://postbox.cloud.yandex.net")
region = os.environ.get("YANDEX_POSTBOX_REGION", "ru-central1")
common = {
    "region_name": region,
    "endpoint_url": endpoint,
    "aws_access_key_id": os.environ["YANDEX_ACCESS_KEY_ID"],
    "aws_secret_access_key": os.environ["YANDEX_SECRET_ACCESS_KEY"],
}

for service in ("ses", "sesv2"):
    try:
        boto3.client(service, **common).get_send_quota()
        print(f"{service}=ok")
    except Exception as exc:
        print(f"{service}=failed:{type(exc).__name__}")
