"""Tests for S3 client service — put_bytes, presigned URLs, dev URLs."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.media.s3_client import S3Client


@pytest.fixture
def s3_client_instance():
    """Create a fresh S3Client for each test."""
    with patch("src.services.media.s3_client.settings") as mock_settings:
        mock_settings.S3_BUCKET = "vie-transcripts"
        mock_settings.AWS_REGION = "us-east-1"
        mock_settings.AWS_ENDPOINT_URL = "http://localhost:4566"
        mock_settings.AWS_ACCESS_KEY_ID = "test-key"
        mock_settings.AWS_SECRET_ACCESS_KEY = "test-secret"
        mock_settings.S3_PRESIGNED_URL_EXPIRY = 3600
        client = S3Client()
        client._bucket = "vie-transcripts"
        yield client


class TestPutBytes:
    """Tests for put_bytes method."""

    @pytest.mark.asyncio
    async def test_put_bytes_success(self, s3_client_instance):
        mock_s3 = AsyncMock()
        mock_s3.put_object = AsyncMock()

        mock_session = MagicMock()
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)
        s3_client_instance._session = mock_session

        await s3_client_instance.put_bytes(
            "videos/abc/frames/30.jpg", b"jpeg-data", content_type="image/jpeg"
        )

        mock_s3.put_object.assert_awaited_once_with(
            Bucket="vie-transcripts",
            Key="videos/abc/frames/30.jpg",
            Body=b"jpeg-data",
            ContentType="image/jpeg",
        )

    @pytest.mark.asyncio
    async def test_put_bytes_default_content_type(self, s3_client_instance):
        mock_s3 = AsyncMock()
        mock_s3.put_object = AsyncMock()

        mock_session = MagicMock()
        mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)
        s3_client_instance._session = mock_session

        await s3_client_instance.put_bytes("some/key", b"data")

        mock_s3.put_object.assert_awaited_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "application/octet-stream"


class TestGeneratePresignedUrl:
    """Tests for generate_presigned_url method."""

    def test_presigned_url_generation(self, s3_client_instance):
        mock_boto3_client = MagicMock()
        mock_boto3_client.generate_presigned_url.return_value = "https://signed.url/test"
        s3_client_instance._sync_client = mock_boto3_client

        url = s3_client_instance.generate_presigned_url("videos/abc/frames/30.jpg")

        assert url == "https://signed.url/test"
        mock_boto3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "vie-transcripts", "Key": "videos/abc/frames/30.jpg"},
            ExpiresIn=3600,
        )

    def test_presigned_url_custom_expiry(self, s3_client_instance):
        mock_boto3_client = MagicMock()
        mock_boto3_client.generate_presigned_url.return_value = "https://signed.url/test"
        s3_client_instance._sync_client = mock_boto3_client

        s3_client_instance.generate_presigned_url("key", expires_in=600)

        mock_boto3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "vie-transcripts", "Key": "key"},
            ExpiresIn=600,
        )


class TestGetDevUrl:
    """Tests for get_dev_url method."""

    def test_dev_url_with_endpoint(self, s3_client_instance):
        with patch("src.services.media.s3_client.settings") as mock_settings:
            mock_settings.AWS_ENDPOINT_URL = "http://localhost:4566"
            url = s3_client_instance.get_dev_url("videos/abc/frames/30.jpg")

        assert url == "http://localhost:4566/vie-transcripts/videos/abc/frames/30.jpg"

    def test_dev_url_without_endpoint(self, s3_client_instance):
        with patch("src.services.media.s3_client.settings") as mock_settings:
            mock_settings.AWS_ENDPOINT_URL = None
            url = s3_client_instance.get_dev_url("videos/abc/frames/30.jpg")

        assert url == "/vie-transcripts/videos/abc/frames/30.jpg"

    def test_dev_url_with_localhost_endpoint(self, s3_client_instance):
        with patch("src.services.media.s3_client.settings") as mock_settings:
            mock_settings.AWS_ENDPOINT_URL = "http://localhost:4566"
            url = s3_client_instance.get_dev_url("key/path")

        assert url == "http://localhost:4566/vie-transcripts/key/path"


def _paged_session(pages: list[dict]) -> tuple[MagicMock, AsyncMock]:
    """Session whose s3 client answers list_objects_v2 with the given pages."""
    mock_s3 = AsyncMock()
    mock_s3.list_objects_v2 = AsyncMock(side_effect=pages)
    mock_s3.delete_objects = AsyncMock(return_value={})
    mock_session = MagicMock()
    mock_session.client.return_value.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_session.client.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_s3


class TestDeletePrefix:
    """Tests for delete_prefix — the S3 half of a global video purge."""

    @pytest.mark.asyncio
    async def test_should_delete_every_page_when_listing_is_truncated(self, s3_client_instance):
        pages = [
            {
                "Contents": [{"Key": f"videos/abc/frames/{i}.jpg"} for i in range(1000)],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            },
            {
                "Contents": [{"Key": f"videos/abc/scenes-v3/{i}.jpg"} for i in range(500)],
                "IsTruncated": False,
            },
        ]
        session, mock_s3 = _paged_session(pages)
        s3_client_instance._session = session

        deleted = await s3_client_instance.delete_prefix("videos/abc/")

        assert deleted == 1500
        assert mock_s3.delete_objects.await_count == 2
        second_list = mock_s3.list_objects_v2.await_args_list[1].kwargs
        assert second_list["ContinuationToken"] == "t1"

    @pytest.mark.asyncio
    async def test_should_delete_nothing_when_prefix_has_no_objects(self, s3_client_instance):
        session, mock_s3 = _paged_session([{"IsTruncated": False}])
        s3_client_instance._session = session

        deleted = await s3_client_instance.delete_prefix("videos/abc/")

        assert deleted == 0
        mock_s3.delete_objects.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_should_split_one_page_into_batches_of_1000(self, s3_client_instance):
        keys = [{"Key": f"videos/abc/frames/{i}.jpg"} for i in range(1001)]
        session, mock_s3 = _paged_session([{"Contents": keys, "IsTruncated": False}])
        s3_client_instance._session = session

        deleted = await s3_client_instance.delete_prefix("videos/abc/")

        assert deleted == 1001
        assert mock_s3.delete_objects.await_count == 2
        first_batch = mock_s3.delete_objects.await_args_list[0].kwargs["Delete"]["Objects"]
        assert len(first_batch) == 1000

    @pytest.mark.asyncio
    async def test_should_raise_when_s3_reports_per_key_errors(self, s3_client_instance):
        session, mock_s3 = _paged_session(
            [{"Contents": [{"Key": "videos/abc/frames/1.jpg"}], "IsTruncated": False}]
        )
        mock_s3.delete_objects = AsyncMock(
            return_value={"Errors": [{"Key": "videos/abc/frames/1.jpg", "Code": "AccessDenied"}]}
        )
        s3_client_instance._session = session

        with pytest.raises(RuntimeError, match="AccessDenied"):
            await s3_client_instance.delete_prefix("videos/abc/")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "prefix",
        ["", "videos/", "backups/x/", "videos/../", "videos/abc", "videos/abc/frames/"],
    )
    async def test_should_refuse_prefixes_outside_one_video(self, s3_client_instance, prefix):
        with pytest.raises(ValueError):
            await s3_client_instance.delete_prefix(prefix)
