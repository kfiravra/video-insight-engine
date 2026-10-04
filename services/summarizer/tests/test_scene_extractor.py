"""Tests for scene-based frame extraction."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.media import download_utils
from src.services.media.scene_extractor import (
    _check_existing_frames,
    _dedupe_refined_frames,
    cleanup_temp_dir,
    extract_scene_keyframes,
    persist_vision_descriptions,
)

# Expected empty result from the 3-tier return shape
EMPTY_RESULT = {"all_frames": [], "selected_frames": [], "gallery_frames": []}


class TestExtractSceneKeyframes:
    """Test scene keyframe extraction."""

    @patch("src.services.media.scene_extractor.settings")
    async def test_returns_empty_when_disabled(self, mock_settings):
        mock_settings.SCENE_EXTRACTION_ENABLED = False

        result = await extract_scene_keyframes("dQw4w9WgXcQ")

        assert result == EMPTY_RESULT

    @patch("src.services.media.scene_extractor.YOUTUBE_ID_RE")
    @patch("src.services.media.scene_extractor.settings")
    async def test_returns_empty_for_invalid_id(self, mock_settings, mock_re):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_re.match.return_value = None

        result = await extract_scene_keyframes("invalid!")

        assert result == EMPTY_RESULT

    @patch(
        "src.services.media.scene_extractor._check_existing_frames",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("src.services.media.scene_extractor.s3_client")
    @patch("src.services.media.scene_extractor.settings")
    @patch("asyncio.create_subprocess_exec")
    async def test_returns_empty_on_ytdlp_timeout(
        self, mock_exec, mock_settings, mock_s3, mock_check
    ):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.SCENE_THRESHOLD = 0.3

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_exec.return_value = mock_proc

        result = await extract_scene_keyframes("dQw4w9WgXcQ")

        assert result == EMPTY_RESULT

    @patch(
        "src.services.media.scene_extractor._check_existing_frames",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("src.services.media.scene_extractor.s3_client")
    @patch("src.services.media.scene_extractor.settings")
    @patch("asyncio.create_subprocess_exec")
    async def test_proxy_travels_in_env_never_on_the_command_line(
        self, mock_exec, mock_settings, mock_s3, mock_check, monkeypatch
    ):
        proxy_url = "http://user:pass@203.0.113.7:8080"
        monkeypatch.setattr(download_utils.settings, "YOUTUBE_PROXY_URL", proxy_url)
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.SCENE_THRESHOLD = 0.3
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_exec.return_value = mock_proc

        await extract_scene_keyframes("dQw4w9WgXcQ")

        assert proxy_url not in mock_exec.call_args.args
        assert mock_exec.call_args.kwargs["env"]["HTTPS_PROXY"] == proxy_url

    @patch(
        "src.services.media.scene_extractor._check_existing_frames",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("src.services.media.scene_extractor.settings")
    async def test_returns_empty_when_ytdlp_missing(self, mock_settings, mock_check):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.SCENE_THRESHOLD = 0.3

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await extract_scene_keyframes("dQw4w9WgXcQ")

        assert result == EMPTY_RESULT

    @patch(
        "src.services.media.scene_extractor._check_existing_frames",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch("src.services.media.scene_extractor.s3_client")
    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.tempfile")
    @patch("asyncio.create_subprocess_exec")
    async def test_returns_empty_on_ytdlp_failure(
        self, mock_exec, mock_tempfile, mock_settings, mock_s3, mock_check
    ):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.SCENE_THRESHOLD = 0.3
        mock_tempfile.mkdtemp.return_value = "/tmp/vie-scene-test"

        # yt-dlp returns non-zero
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_exec.return_value = mock_proc

        with patch("src.services.media.scene_extractor.Path") as mock_path:
            mock_video = MagicMock()
            mock_video.exists.return_value = False
            mock_frames_dir = MagicMock()
            mock_path.return_value.__truediv__ = MagicMock(
                side_effect=[mock_video, mock_frames_dir]
            )

            result = await extract_scene_keyframes("dQw4w9WgXcQ")

        assert result == EMPTY_RESULT

    @patch("src.services.media.scene_extractor._check_existing_frames", new_callable=AsyncMock)
    @patch("src.services.media.scene_extractor.settings")
    async def test_s3_cache_hit_skips_extraction(self, mock_settings, mock_check):
        """When the S3 manifest yields a cache hit, return it without FFmpeg."""
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        frames = [
            {"index": i, "s3_key": f"scene_{i}.jpg", "s3_url": f"url_{i}", "timestamp": i * 10.0}
            for i in range(15)
        ]
        mock_check.return_value = {
            "all_frames": frames,
            "selected_frames": frames,
            "gallery_frames": frames[:12],
        }

        result = await extract_scene_keyframes("dQw4w9WgXcQ")

        assert len(result["all_frames"]) == 15
        assert len(result["gallery_frames"]) == 12
        assert result["all_frames"][3]["timestamp"] == 30.0


def _manifest_entry(i: int, ts: float | None = None) -> dict:
    return {
        "index": i,
        "filename": f"scene_{i:04d}.jpg",
        "s3Key": f"videos/dQw4w9WgXcQ/scenes-v2/scene_{i:04d}.jpg",
        "timestamp": ts if ts is not None else i * 12.5,
    }


def _valid_manifest(count: int = 15, hires_count: int | None = None) -> dict:
    return {
        "version": 2,
        "videoId": "dQw4w9WgXcQ",
        # None → healthy default (all frames upgraded); pass 0 for degraded runs.
        "hiresCount": count if hires_count is None else hires_count,
        "frames": [_manifest_entry(i) for i in range(count)],
        "galleryIndices": list(range(0, count, 2)),
    }


class TestManifestCache:
    """Frame cache reuse is manifest-only — no manifest means re-extract."""

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_reads_manifest_at_versioned_prefix(self, mock_s3_client, mock_settings):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        mock_s3_client.get_json = AsyncMock(return_value=None)

        result = await _check_existing_frames("dQw4w9WgXcQ")

        assert result is None
        assert (
            mock_s3_client.get_json.await_args.args[0]
            == "videos/dQw4w9WgXcQ/scenes-v2/manifest.json"
        )

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_valid_manifest_returns_timestamps_and_gallery(
        self, mock_s3_client, mock_settings
    ):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        mock_s3_client.get_json = AsyncMock(return_value=_valid_manifest(15))
        mock_s3_client.generate_presigned_url.side_effect = lambda key: f"https://s3/{key}"

        result = await _check_existing_frames("dQw4w9WgXcQ")

        assert result is not None
        assert len(result["all_frames"]) == 15
        assert result["all_frames"][4]["timestamp"] == 50.0
        assert result["all_frames"][4]["s3_url"].startswith("https://s3/")
        # Gallery honors manifest galleryIndices (even indices)
        assert [f["index"] for f in result["gallery_frames"]] == list(range(0, 15, 2))

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_invalid_manifests_are_cache_misses(self, mock_s3_client, mock_settings):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        wrong_version = {**_valid_manifest(), "version": 99}
        too_few = _valid_manifest(5)
        all_zero = {
            **_valid_manifest(),
            "frames": [_manifest_entry(i, ts=0.0) for i in range(15)],
        }
        missing_key = {
            **_valid_manifest(),
            "frames": [{"index": 0, "timestamp": 5.0}] * 15,
        }

        for bad in (wrong_version, too_few, all_zero, missing_key):
            mock_s3_client.get_json = AsyncMock(return_value=bad)
            assert await _check_existing_frames("dQw4w9WgXcQ") is None

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_cache_hit_restores_persisted_vision_descriptions(
        self, mock_s3_client, mock_settings
    ):
        """Cached frames have no local path, so vision can't re-run — the
        descriptions stored after the fresh run must come back with the hit."""
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        descriptions = [{"original_index": 2, "scene_type": "slide", "content": "chart"}]
        mock_s3_client.get_json = AsyncMock(
            return_value={**_valid_manifest(15), "visionDescriptions": descriptions}
        )
        mock_s3_client.generate_presigned_url.side_effect = lambda key: f"https://s3/{key}"

        result = await _check_existing_frames("dQw4w9WgXcQ")

        assert result is not None
        assert result["vision_descriptions"] == descriptions

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_cache_hit_without_descriptions_yields_empty_list(
        self, mock_s3_client, mock_settings
    ):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        mock_s3_client.get_json = AsyncMock(return_value=_valid_manifest(15))
        mock_s3_client.generate_presigned_url.side_effect = lambda key: f"https://s3/{key}"

        result = await _check_existing_frames("dQw4w9WgXcQ")

        assert result is not None
        assert result["vision_descriptions"] == []


class TestPersistVisionDescriptions:
    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_read_modify_writes_manifest(self, mock_s3_client, mock_settings):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        manifest = _valid_manifest(15)
        mock_s3_client.get_json = AsyncMock(return_value=manifest)
        mock_s3_client.put_json = AsyncMock()
        descriptions = [{"original_index": 0, "scene_type": "demo"}]

        await persist_vision_descriptions("dQw4w9WgXcQ", descriptions)

        key, written = mock_s3_client.put_json.await_args.args
        assert key == "videos/dQw4w9WgXcQ/scenes-v2/manifest.json"
        assert written["visionDescriptions"] == descriptions
        assert written["frames"] == manifest["frames"]  # rest of the manifest intact

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_no_manifest_or_empty_descriptions_is_noop(self, mock_s3_client, mock_settings):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        mock_s3_client.get_json = AsyncMock(return_value=None)
        mock_s3_client.put_json = AsyncMock()

        await persist_vision_descriptions("dQw4w9WgXcQ", [{"scene_type": "x"}])
        await persist_vision_descriptions("dQw4w9WgXcQ", [])

        mock_s3_client.put_json.assert_not_awaited()

    @patch("src.services.media.scene_extractor.settings")
    @patch("src.services.media.scene_extractor.s3_client")
    async def test_s3_failure_is_swallowed(self, mock_s3_client, mock_settings):
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        mock_s3_client.get_json = AsyncMock(side_effect=RuntimeError("s3 down"))

        await persist_vision_descriptions("dQw4w9WgXcQ", [{"scene_type": "x"}])  # no raise


class TestTwoPassExtraction:
    """Happy path: detection settings from config, refine before upload, versioned keys."""

    @patch(
        "src.services.media.scene_extractor._check_existing_frames",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "src.services.media.scene_extractor._upload_frames_batch",
        new_callable=AsyncMock,
        side_effect=lambda frames: frames,
    )
    @patch(
        "src.services.media.hires_refiner.refine_selected_frames",
        new_callable=AsyncMock,
        return_value=2,
    )
    @patch("src.services.media.frame_scorer.select_frames")
    @patch("src.services.media.frame_scorer.score_all_frames")
    @patch("src.services.media.scene_extractor.s3_client")
    @patch("src.services.media.scene_extractor.tempfile")
    @patch("src.services.media.scene_extractor.settings")
    @patch("asyncio.create_subprocess_exec")
    async def test_happy_path_refines_then_uploads_versioned_keys(
        self,
        mock_exec,
        mock_settings,
        mock_tempfile,
        mock_s3_client,
        mock_score,
        mock_select,
        mock_refine,
        mock_upload,
        mock_check,
        tmp_path,
    ):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.SCENE_THRESHOLD = 0.3
        mock_settings.SCENE_DETECT_SCALE_WIDTH = 1024
        mock_settings.SCENE_JPEG_QUALITY = 4
        mock_settings.SCENE_S3_PREFIX = "scenes-v2"
        mock_tempfile.mkdtemp.return_value = str(tmp_path)

        video_id = "dQw4w9WgXcQ"

        async def ytdlp_communicate():
            (tmp_path / f"{video_id}.mp4").write_bytes(b"x" * 2048)
            return (b"", b"")

        async def ffmpeg_communicate():
            for i in (1, 2):
                (tmp_path / "frames" / f"scene_{i:04d}.jpg").write_bytes(b"jpg")
            return (b"", b"pts_time: 1.5\npts_time: 3.0\n")

        def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = ytdlp_communicate if args[0] == "yt-dlp" else ffmpeg_communicate
            return proc

        mock_exec.side_effect = fake_exec
        mock_score.side_effect = lambda frames: frames
        mock_select.side_effect = lambda frames, duration: (frames, frames[:1])
        mock_s3_client.put_json = AsyncMock()

        result = await extract_scene_keyframes(video_id, duration_seconds=120)

        # Detection ffmpeg args come from config (never-odd height via :-2)
        ffmpeg_args = next(c.args for c in mock_exec.call_args_list if c.args[0] == "ffmpeg")
        vf_arg = ffmpeg_args[ffmpeg_args.index("-vf") + 1]
        assert "scale=1024:-2" in vf_arg
        assert ffmpeg_args[ffmpeg_args.index("-q:v") + 1] == "4"

        # Hi-res refinement runs on the selected frames before upload
        mock_refine.assert_awaited_once()
        assert mock_refine.await_args.args[0] == video_id

        # Uploaded keys use the versioned prefix
        assert len(result["selected_frames"]) == 2
        for frame in result["selected_frames"]:
            assert frame["s3_key"].startswith(f"videos/{video_id}/scenes-v2/scene_")

        # Manifest is written alongside the frames with real timestamps
        mock_s3_client.put_json.assert_awaited_once()
        manifest_key, manifest = mock_s3_client.put_json.await_args.args
        assert manifest_key == f"videos/{video_id}/scenes-v2/manifest.json"
        assert manifest["version"] == 2
        assert isinstance(manifest["hiresCount"], int)
        assert [f["timestamp"] for f in manifest["frames"]] == [1.5, 3.0]
        assert all(
            f["s3Key"].startswith(f"videos/{video_id}/scenes-v2/") for f in manifest["frames"]
        )


class TestDedupeRefinedFrames:
    """Post-hires dedup: revert colliding refined frames, drop true duplicates."""

    @staticmethod
    def _write_jpg(path, color, noise_at=None):
        from PIL import Image

        img = Image.new("L", (64, 64), color)
        if noise_at is not None:
            for x in range(0, 64, 2):
                for y in range(0, 64, 2):
                    img.putpixel((x, y), (x * 4 + noise_at) % 256)
        img.save(path, "JPEG")

    def test_distinct_frames_untouched(self, tmp_path):
        paths = []
        for i, color in enumerate((0, 128, 255)):
            p = tmp_path / f"scene_{i:04d}.jpg"
            self._write_jpg(p, color, noise_at=i * 40)
            paths.append(p)
        frames = [
            {"index": i, "path": str(p), "timestamp": float(i * 10)} for i, p in enumerate(paths)
        ]

        result = _dedupe_refined_frames(frames)

        assert result == frames

    def test_duplicate_hires_reverts_to_distinct_original(self, tmp_path):
        # Frame 0 and frame 1's hires versions are identical; frame 1's
        # original low-res is distinct -> frame 1 reverts to the original.
        low0 = tmp_path / "scene_0000.jpg"
        low1 = tmp_path / "scene_0001.jpg"
        self._write_jpg(low0, 200)
        self._write_jpg(low1, 30, noise_at=17)
        hires0 = tmp_path / "scene_0000.jpg.hires.jpg"
        hires1 = tmp_path / "scene_0001.jpg.hires.jpg"
        self._write_jpg(hires0, 200)
        self._write_jpg(hires1, 200)
        frames = [
            {"index": 0, "path": str(hires0), "timestamp": 10.0},
            {"index": 1, "path": str(hires1), "timestamp": 20.0},
        ]

        result = _dedupe_refined_frames(frames)

        assert len(result) == 2
        assert result[1]["path"] == str(low1)

    def test_duplicate_after_revert_is_dropped(self, tmp_path):
        # Both hires AND both originals are identical -> second frame dropped.
        low0 = tmp_path / "scene_0000.jpg"
        low1 = tmp_path / "scene_0001.jpg"
        self._write_jpg(low0, 200)
        self._write_jpg(low1, 200)
        hires0 = tmp_path / "scene_0000.jpg.hires.jpg"
        hires1 = tmp_path / "scene_0001.jpg.hires.jpg"
        self._write_jpg(hires0, 200)
        self._write_jpg(hires1, 200)
        frames = [
            {"index": 0, "path": str(hires0), "timestamp": 10.0},
            {"index": 1, "path": str(hires1), "timestamp": 20.0},
        ]

        result = _dedupe_refined_frames(frames)

        assert [f["index"] for f in result] == [0]

    def test_unreadable_frame_kept_fail_open(self, tmp_path):
        good = tmp_path / "scene_0000.jpg"
        self._write_jpg(good, 100)
        frames = [
            {"index": 0, "path": str(good), "timestamp": 10.0},
            {"index": 1, "path": str(tmp_path / "missing.jpg"), "timestamp": 20.0},
        ]

        result = _dedupe_refined_frames(frames)

        assert len(result) == 2


class TestCleanupTempDir:
    """Test temp directory cleanup."""

    @patch("src.services.media.scene_extractor.shutil")
    async def test_cleanup_calls_rmtree(self, mock_shutil):
        await cleanup_temp_dir("/tmp/vie-scene-test")
        mock_shutil.rmtree.assert_called_once_with("/tmp/vie-scene-test", ignore_errors=True)

    @patch("src.services.media.scene_extractor.shutil")
    async def test_cleanup_handles_errors(self, mock_shutil):
        mock_shutil.rmtree.side_effect = Exception("cleanup failed")

        # Should not raise
        await cleanup_temp_dir("/tmp/vie-scene-nonexistent")


class TestReselectHook:
    """HIGH-tier hook runs between selection and hires refinement."""

    async def test_hook_receives_overselected_candidates_and_narrows(self):
        from src.services.media.scene_extractor import _sample_by_time

        # Unit-level check of the reselect contract used in _do_extraction:
        # over-sample -> hook filters -> trim to keep_count.
        scored = [
            {"index": i, "timestamp": float(i * 10), "total_score": 0.5, "path": f"/f{i}.jpg"}
            for i in range(60)
        ]
        candidates = _sample_by_time(scored, 40)
        assert len(candidates) == 40

        async def hook(frames):
            return [f for f in frames if f["index"] % 2 == 0]

        kept = await hook(candidates)
        final = _sample_by_time(kept, 25)
        assert len(final) <= 25
        assert all(f["index"] % 2 == 0 for f in final)

    @patch(
        "src.services.media.scene_extractor._check_existing_frames",
        new_callable=AsyncMock,
        return_value=None,
    )
    @patch(
        "src.services.media.scene_extractor._upload_frames_batch",
        new_callable=AsyncMock,
        side_effect=lambda frames: frames,
    )
    @patch(
        "src.services.media.hires_refiner.refine_selected_frames",
        new_callable=AsyncMock,
        return_value=0,
    )
    @patch("src.services.media.frame_scorer.select_frames")
    @patch("src.services.media.frame_scorer.score_all_frames")
    @patch("src.services.media.scene_extractor.s3_client")
    @patch("src.services.media.scene_extractor.tempfile")
    @patch("src.services.media.scene_extractor.settings")
    @patch("asyncio.create_subprocess_exec")
    async def test_hook_failure_falls_back_to_local_selection(
        self,
        mock_exec,
        mock_settings,
        mock_tempfile,
        mock_s3_client,
        mock_score,
        mock_select,
        mock_refine,
        mock_upload,
        mock_check,
        tmp_path,
    ):
        mock_settings.SCENE_EXTRACTION_ENABLED = True
        mock_settings.SCENE_THRESHOLD = 0.3
        mock_settings.SCENE_DETECT_SCALE_WIDTH = 1024
        mock_settings.SCENE_JPEG_QUALITY = 4
        mock_settings.SCENE_S3_PREFIX = "scenes-v3"
        mock_tempfile.mkdtemp.return_value = str(tmp_path)
        mock_s3_client.put_json = AsyncMock()

        video_id = "dQw4w9WgXcQ"

        async def ytdlp_communicate():
            (tmp_path / f"{video_id}.mp4").write_bytes(b"x" * 2048)
            return (b"", b"")

        async def ffmpeg_communicate():
            for i in (1, 2, 3):
                (tmp_path / "frames" / f"scene_{i:04d}.jpg").write_bytes(b"jpg")
            return (b"", b"pts_time: 1.5\npts_time: 3.0\npts_time: 4.5\n")

        def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = ytdlp_communicate if args[0] == "yt-dlp" else ffmpeg_communicate
            return proc

        mock_exec.side_effect = fake_exec
        mock_score.side_effect = lambda frames: frames
        mock_select.side_effect = lambda frames, duration: (frames, frames[:1])

        async def bad_hook(frames):
            raise RuntimeError("vision exploded")

        from src.services.media.scene_extractor import extract_scene_keyframes

        result = await extract_scene_keyframes(
            video_id,
            duration_seconds=120,
            overselect_count=40,
            reselect_hook=bad_hook,
        )

        # Local selection survives the hook failure
        assert len(result["selected_frames"]) == 3


class TestStaticCameraFallback:
    async def test_interval_frames_shaped_like_scene_frames(self, tmp_path):
        from unittest.mock import patch

        from PIL import Image

        from src.services.media.scene_extractor import _sample_interval_frames

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        async def fake_exec(*args, **kwargs):
            # Simulate ffmpeg writing interval frames
            for i in range(1, 5):
                Image.new("RGB", (16, 16), (i * 40, 0, 0)).save(
                    frames_dir / f"interval_{i:04d}.jpg", "JPEG"
                )
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            frames = await _sample_interval_frames(
                tmp_path / "v.mp4", frames_dir, 600, str(tmp_path), index_offset=6
            )

        assert len(frames) == 4
        assert frames[0]["index"] == 6
        # Regression: fps=1/step samples window STARTS (frame i at i*step) —
        # a +step/2 offset here made the hires refiner seek the wrong content.
        from src.services.media.scene_extractor import _INTERVAL_SAMPLE_COUNT

        step = max(15, 600 // _INTERVAL_SAMPLE_COUNT)
        assert [f["timestamp"] for f in frames] == [
            0.0,
            float(step),
            float(2 * step),
            float(3 * step),
        ]
        assert all(f["path"].endswith(".jpg") for f in frames)

    async def test_ffmpeg_failure_returns_empty(self, tmp_path):
        from unittest.mock import patch

        from src.services.media.scene_extractor import _sample_interval_frames

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            frames = await _sample_interval_frames(
                tmp_path / "v.mp4", frames_dir, 600, str(tmp_path), index_offset=0
            )

        assert frames == []


class TestManifestQualityGate:
    """v2 manifests carry hiresCount; degraded (0-upgrade) runs re-extract."""

    @patch("src.services.media.scene_extractor.settings")
    def test_v1_manifest_rejected(self, mock_settings):
        from src.services.media.scene_extractor import _validate_manifest

        mock_settings.SCENE_HIRES_ENABLED = True
        manifest = _valid_manifest(15)
        manifest["version"] = 1
        del manifest["hiresCount"]

        assert _validate_manifest(manifest) is None

    @patch("src.services.media.scene_extractor.settings")
    def test_degraded_manifest_rejected_when_hires_enabled(self, mock_settings):
        from src.services.media.scene_extractor import _validate_manifest

        mock_settings.SCENE_HIRES_ENABLED = True

        assert _validate_manifest(_valid_manifest(15, hires_count=0)) is None

    @patch("src.services.media.scene_extractor.settings")
    def test_degraded_manifest_accepted_when_hires_disabled(self, mock_settings):
        from src.services.media.scene_extractor import _validate_manifest

        mock_settings.SCENE_HIRES_ENABLED = False

        assert _validate_manifest(_valid_manifest(15, hires_count=0)) is not None

    @patch("src.services.media.scene_extractor.settings")
    def test_healthy_manifest_accepted(self, mock_settings):
        from src.services.media.scene_extractor import _validate_manifest

        mock_settings.SCENE_HIRES_ENABLED = True

        entries = _validate_manifest(_valid_manifest(15, hires_count=12))
        assert entries is not None
        assert len(entries) == 15
