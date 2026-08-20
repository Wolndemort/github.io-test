import os
import boto3

client = boto3.client(
    "sesv2",
    region_name=os.getenv("YANDEX_POSTBOX_REGION", "ru-central1"),
    endpoint_url=os.getenv("YANDEX_POSTBOX_ENDPOINT", "https://postbox.cloud.yandex.net"),
    aws_access_key_id=os.environ["YANDEX_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["YANDEX_SECRET_ACCESS_KEY"],
)
try:
    client.send_email(
        FromEmailAddress=os.environ["EMAIL_FROM_EMAIL"],
        Destination={"ToAddresses": ["omarovadam405@gmail.com"]},
        Content={"Simple": {"Subject": {"Data": "SpeedyCRM Postbox probe"}, "Body": {"Text": {"Data": "Staging delivery test."}}}},
    )
    print("send=ok")
except Exception as exc:
    print(f"send=failed:{type(exc).__name__}:{str(exc)[:160]}")
