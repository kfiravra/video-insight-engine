"""S3 client service for media storage.

Provides async S3 operations using aioboto3 for storing and retrieving
transcripts, frames, and other media. Uses AWS S3 for storing transcripts,
frames, and other media.

Note: aioboto3 is imported lazily to allow graceful degradation if not installed.
"""

import json
import logging
import re
from typing import Any

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings

logger = logging.getLogger(__name__)

# Track if aioboto3 is available
_aioboto3_available: bool | None = None


def _is_retryable_s3_error(exc: BaseException) -> bool:
    """Retry on network errors and AWS client errors, not on programming errors."""
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    try:
        from botocore.exceptions import BotoCoreError, ClientError

        return isinstance(exc, (ClientError, BotoCoreError))
    except ImportError:
        return False


def _check_aioboto3() -> bool:
    """Check if aioboto3 is available (lazy check)."""
    global _aioboto3_available
    if _aioboto3_available is None:
        try:
            import aioboto3  # noqa: F401

            _aioboto3_available = True
        except ImportError:
            logger.warning("aioboto3 not installed - S3 storage will be disabled")
            _aioboto3_available = False
    return _aioboto3_available


# Only one video's own prefix may be bulk-deleted. A caller bug must fail
# loudly here instead of listing (and deleting) the whole bucket.
_DELETABLE_PREFIX = re.compile(r"^videos/[A-Za-z0-9_-]{1,64}/$")
# S3 accepts at most 1000 keys per delete_objects call.
_DELETE_BATCH_SIZE = 1000


class S3Client:
    """Async S3 client for media storage operations."""

    def __init__(self):
        self._session = None
        self._sync_client = None
        self._bucket = settings.S3_BUCKET

    def _ensure_session(self) -> Any:
        """Lazily initialize the aioboto3 session."""
        if self._session is None:
            if not _check_aioboto3():
                raise RuntimeError("aioboto3 not available - cannot use S3 storage")
            import aioboto3

            self._session = aioboto3.Session()
        return self._session

    def _get_client_config(self) -> dict[str, Any]:
        """Get S3 client configuration."""
        config: dict[str, Any] = {
            "region_name": settings.AWS_REGION,
        }

        # Use custom endpoint (LocalStack) if configured
        if settings.AWS_ENDPOINT_URL:
            config["endpoint_url"] = settings.AWS_ENDPOINT_URL

        # Use explicit credentials if provided
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            config["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            config["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

        return config

    async def ensure_bucket_exists(self) -> None:
        """Create the bucket if it doesn't exist (for LocalStack)."""
        from botocore.exceptions import ClientError

        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
                logger.debug("S3 bucket exists: %s", self._bucket)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("404", "NoSuchBucket"):
                    logger.info("Creating S3 bucket: %s", self._bucket)
                    # us-east-1 doesn't need LocationConstraint, others do
                    create_config: dict[str, Any] = {"Bucket": self._bucket}
                    if settings.AWS_REGION and settings.AWS_REGION != "us-east-1":
                        create_config["CreateBucketConfiguration"] = {
                            "LocationConstraint": settings.AWS_REGION
                        }
                    await s3.create_bucket(**create_config)
                else:
                    raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_s3_error),
        before_sleep=lambda retry_state: logger.warning(
            "S3 put_json retry %d/3: %s",
            retry_state.attempt_number,
            retry_state.outcome.exception(),
        ),
        reraise=True,
    )
    async def put_json(self, key: str, data: dict[str, Any]) -> None:
        """
        Upload JSON data to S3 with retry logic for transient errors.

        Args:
            key: S3 object key (e.g., "videos/abc123/transcript.json")
            data: Dictionary to serialize as JSON

        Raises:
            ClientError: If upload fails after retries
        """
        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            body = json.dumps(data, ensure_ascii=False)
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
            logger.debug("Uploaded to S3: %s (%d bytes)", key, len(body))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_s3_error),
        before_sleep=lambda retry_state: logger.warning(
            "S3 put_bytes retry %d/3: %s",
            retry_state.attempt_number,
            retry_state.outcome.exception(),
        ),
        reraise=True,
    )
    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        """Upload binary data to S3 with retry logic.

        Args:
            key: S3 object key (e.g., "videos/abc123/frames/30.jpg")
            data: Raw bytes to upload
            content_type: MIME type (default: application/octet-stream)
        """
        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            logger.debug("Uploaded to S3: %s (%d bytes)", key, len(data))

    def _ensure_sync_client(self) -> Any:
        """Lazily initialize the sync boto3 client for presigned URLs."""
        if self._sync_client is None:
            try:
                import boto3
            except ImportError:
                raise RuntimeError("boto3 not available - cannot generate presigned URLs")
            config = self._get_client_config()
            self._sync_client = boto3.client("s3", **config)
        return self._sync_client

    def generate_presigned_url(self, key: str, expires_in: int | None = None) -> str:
        """Generate a presigned URL for downloading an S3 object.

        This is a sync method — presigned URL generation is a local
        cryptographic operation (HMAC-SHA256), no network call needed.

        Args:
            key: S3 object key
            expires_in: URL validity in seconds (default: settings.S3_PRESIGNED_URL_EXPIRY)

        Returns:
            Presigned URL string
        """
        client = self._ensure_sync_client()
        if expires_in is None:
            expires_in = settings.S3_PRESIGNED_URL_EXPIRY
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url

    def get_dev_url(self, key: str) -> str:
        """Get a direct URL for custom endpoint access (no presigning).

        Reserved for CI/CD testing with LocalStack. Returns a
        direct URL using the host-accessible endpoint.

        Args:
            key: S3 object key

        Returns:
            Direct URL string
        """
        from urllib.parse import quote

        encoded_key = quote(key, safe="/")
        endpoint = settings.AWS_ENDPOINT_URL
        if not endpoint:
            logger.warning("get_dev_url called without AWS_ENDPOINT_URL set")
            return f"/{self._bucket}/{encoded_key}"
        return f"{endpoint}/{self._bucket}/{encoded_key}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_s3_error),
        before_sleep=lambda retry_state: logger.warning(
            "S3 get_json retry %d/3: %s",
            retry_state.attempt_number,
            retry_state.outcome.exception(),
        ),
        reraise=True,
    )
    async def get_json(self, key: str) -> dict[str, Any] | None:
        """
        Download and parse JSON from S3 with retry logic for transient errors.

        Args:
            key: S3 object key

        Returns:
            Parsed JSON dict, or None if object doesn't exist

        Raises:
            ClientError: If download fails after retries (except for NoSuchKey)
        """
        from botocore.exceptions import ClientError

        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            try:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
                async with response["Body"] as stream:
                    body = await stream.read()
                    return json.loads(body.decode("utf-8"))
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "NoSuchKey":
                    logger.debug("S3 object not found: %s", key)
                    return None
                raise

    async def exists(self, key: str) -> bool:
        """
        Check if an object exists in S3.

        Args:
            key: S3 object key

        Returns:
            True if object exists, False otherwise
        """
        from botocore.exceptions import ClientError

        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "404":
                    return False
                raise

    async def delete(self, key: str) -> None:
        """
        Delete an object from S3.

        Args:
            key: S3 object key

        Raises:
            ClientError: If deletion fails
        """
        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)
            logger.debug("Deleted from S3: %s", key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_s3_error),
        before_sleep=lambda retry_state: logger.warning(
            "S3 delete_prefix retry %d/3: %s",
            retry_state.attempt_number,
            retry_state.outcome.exception(),
        ),
        reraise=True,
    )
    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under one video's prefix. Returns the deleted count.

        Pages through ``list_objects_v2`` by hand (no paginator) so each page
        turns into ``delete_objects`` batches of at most 1000 keys.

        Raises:
            ValueError: If ``prefix`` is not a single ``videos/<id>/`` prefix.
            RuntimeError: If S3 reports per-key delete errors.
        """
        if not _DELETABLE_PREFIX.match(prefix):
            raise ValueError(f"Refusing to delete outside a single video prefix: {prefix!r}")
        deleted = 0
        session = self._ensure_session()
        async with session.client("s3", **self._get_client_config()) as s3:
            token: str | None = None
            while True:
                page = await s3.list_objects_v2(**self._list_args(prefix, token))
                keys = [obj["Key"] for obj in page.get("Contents", [])]
                if keys:
                    deleted += await self._delete_keys(s3, keys)
                token = page.get("NextContinuationToken")
                if not page.get("IsTruncated") or not token:
                    break
        logger.info("Deleted %d object(s) under %s", deleted, prefix)
        return deleted

    def _list_args(self, prefix: str, token: str | None) -> dict[str, Any]:
        args: dict[str, Any] = {
            "Bucket": self._bucket,
            "Prefix": prefix,
            "MaxKeys": _DELETE_BATCH_SIZE,
        }
        if token:
            args["ContinuationToken"] = token
        return args

    async def _delete_keys(self, s3: Any, keys: list[str]) -> int:
        deleted = 0
        for start in range(0, len(keys), _DELETE_BATCH_SIZE):
            batch = keys[start : start + _DELETE_BATCH_SIZE]
            response = await s3.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            errors = response.get("Errors") or []
            if errors:
                raise RuntimeError(
                    f"S3 delete_objects reported {len(errors)} error(s); first: {errors[0]}"
                )
            deleted += len(batch)
        return deleted

    async def health_check(self) -> dict[str, Any]:
        """
        Check S3 connectivity and bucket access.

        Returns:
            Health check result with status and details
        """
        if not _check_aioboto3():
            return {
                "status": "unavailable",
                "bucket": self._bucket,
                "error": "aioboto3 not installed",
            }

        from botocore.exceptions import ClientError

        try:
            session = self._ensure_session()
            async with session.client("s3", **self._get_client_config()) as s3:
                await s3.head_bucket(Bucket=self._bucket)
                return {
                    "status": "healthy",
                    "bucket": self._bucket,
                    "endpoint": settings.AWS_ENDPOINT_URL or "aws",
                }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            return {
                "status": "unhealthy",
                "bucket": self._bucket,
                "error": error_code,
                "message": str(e),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "bucket": self._bucket,
                "error": type(e).__name__,
                "message": str(e),
            }

    @staticmethod
    def is_available() -> bool:
        """Check if S3 storage is available (aioboto3 installed)."""
        return _check_aioboto3()


# Singleton instance
s3_client = S3Client()
