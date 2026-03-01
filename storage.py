import os
import logging
import boto3
from botocore.client import Config

logger = logging.getLogger("storage")


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY"],
        aws_secret_access_key=os.environ["R2_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_recording(local_path: str, room_name: str) -> str | None:
    """Upload a local audio file to R2. Returns public URL or None."""
    bucket = os.environ.get("R2_BUCKET", "call-recordings")
    key = f"recordings/{room_name}.ogg"
    try:
        client = get_r2_client()
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=f,
                ContentType="audio/ogg",
            )
        public_url = f"{os.environ['R2_PUBLIC_URL']}/{key}"
        logger.info(f"[R2] Uploaded recording: {public_url}")
        return public_url
    except Exception as e:
        logger.error(f"[R2] Upload failed: {e}")
        return None


def delete_recording(room_name: str) -> None:
    """Delete a recording from R2."""
    bucket = os.environ.get("R2_BUCKET", "call-recordings")
    key = f"recordings/{room_name}.ogg"
    try:
        client = get_r2_client()
        client.delete_object(Bucket=bucket, Key=key)
        logger.info(f"[R2] Deleted: {key}")
    except Exception as e:
        logger.warning(f"[R2] Delete failed: {e}")
