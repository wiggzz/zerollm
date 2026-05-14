"""Unit tests for model seeding helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_seed_models():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "seed_models.py"
    spec = importlib.util.spec_from_file_location("seed_models", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_hf_transfer_environment_sets_fast_xet_defaults(monkeypatch):
    seed_models = _load_seed_models()
    monkeypatch.delenv("HF_XET_HIGH_PERFORMANCE", raising=False)
    monkeypatch.delenv("HF_XET_NUM_CONCURRENT_RANGE_GETS", raising=False)

    seed_models.configure_hf_transfer_environment()

    assert seed_models.os.environ["HF_XET_HIGH_PERFORMANCE"] == "1"
    assert seed_models.os.environ["HF_XET_NUM_CONCURRENT_RANGE_GETS"] == "8"


def test_configure_hf_transfer_environment_preserves_overrides(monkeypatch):
    seed_models = _load_seed_models()
    monkeypatch.setenv("HF_XET_HIGH_PERFORMANCE", "0")
    monkeypatch.setenv("HF_XET_NUM_CONCURRENT_RANGE_GETS", "24")

    seed_models.configure_hf_transfer_environment()

    assert seed_models.os.environ["HF_XET_HIGH_PERFORMANCE"] == "0"
    assert seed_models.os.environ["HF_XET_NUM_CONCURRENT_RANGE_GETS"] == "24"


def test_load_models_reads_repo_manifest():
    seed_models = _load_seed_models()

    models = seed_models.load_models()

    assert [model["name"] for model in models] == [
        "Qwen/Qwen3.6-27B",
        "Qwen/Qwen3.5-4B",
    ]
    assert models[0]["hf_repo"] == "unsloth/Qwen3.6-27B-GGUF"
    assert models[0]["hf_file"] == "Qwen3.6-27B-Q4_K_M.gguf"
    assert models[0]["instance_type"] == "g6e.2xlarge"
    assert "--ctx-size 262144" in models[0]["vllm_args"]
    assert "--spec-default" in models[0]["vllm_args"]


class _FakeTable:
    def __init__(self):
        self.deleted = []

    def scan(self, **kwargs):
        return {
            "Items": [
                {"name": "Qwen/Qwen3.6-27B"},
                {"name": "Qwen/Qwen3.5-27B"},
                {"name": "Qwen/Qwen3.5-4B"},
            ]
        }

    def delete_item(self, **kwargs):
        self.deleted.append(kwargs["Key"]["name"])


def test_prune_stale_models_deletes_rows_absent_from_manifest():
    seed_models = _load_seed_models()
    table = _FakeTable()

    deleted = seed_models.prune_stale_models(
        table,
        {"Qwen/Qwen3.6-27B", "Qwen/Qwen3.5-4B"},
    )

    assert deleted == ["Qwen/Qwen3.5-27B"]
    assert table.deleted == ["Qwen/Qwen3.5-27B"]


class _FakeRemoteFile:
    def __init__(self, data: bytes = b"model-bytes"):
        self._data = data
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FakeHfFileSystem:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.open_calls = []
        _FakeHfFileSystem.instances.append(self)

    def open(self, *args, **kwargs):
        self.open_calls.append((args, kwargs))
        return _FakeRemoteFile()


class _FakeS3:
    class exceptions:
        ClientError = RuntimeError

    def __init__(self):
        self.uploads = []
        self.completed = []
        self.aborted = []

    def head_object(self, **kwargs):
        raise self.exceptions.ClientError("missing")

    def create_multipart_upload(self, **kwargs):
        self.uploads.append(("create", kwargs))
        return {"UploadId": "upload-1"}

    def upload_part(self, **kwargs):
        self.uploads.append(("part", kwargs))
        return {"ETag": f"etag-{kwargs['PartNumber']}"}

    def complete_multipart_upload(self, **kwargs):
        self.completed.append(kwargs)

    def abort_multipart_upload(self, **kwargs):
        self.aborted.append(kwargs)


def test_upload_model_streams_huggingface_file_to_s3(monkeypatch):
    seed_models = _load_seed_models()
    fake_s3 = _FakeS3()
    _FakeHfFileSystem.instances = []

    import boto3
    import huggingface_hub

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: fake_s3)
    monkeypatch.setattr(huggingface_hub, "HfFileSystem", _FakeHfFileSystem)
    monkeypatch.setenv("HF_TOKEN", "hf-secret")

    seed_models.upload_model(
        {
            "name": "test-model",
            "hf_repo": "org/repo",
            "hf_file": "model.gguf",
            "hf_revision": "abc123",
        },
        bucket="models-bucket",
        region="us-east-2",
    )

    assert _FakeHfFileSystem.instances[0].kwargs == {
        "token": "hf-secret",
        "block_size": 0,
    }
    assert _FakeHfFileSystem.instances[0].open_calls == [
        (("org/repo/model.gguf", "rb"), {"revision": "abc123", "block_size": 0})
    ]
    assert fake_s3.uploads[0] == (
        "create",
        {"Bucket": "models-bucket", "Key": "model.gguf"},
    )
    assert fake_s3.uploads[1] == (
        "part",
        {
            "Bucket": "models-bucket",
            "Key": "model.gguf",
            "UploadId": "upload-1",
            "PartNumber": 1,
            "Body": b"model-bytes",
        },
    )
    assert fake_s3.completed == [
        {
            "Bucket": "models-bucket",
            "Key": "model.gguf",
            "UploadId": "upload-1",
            "MultipartUpload": {"Parts": [{"PartNumber": 1, "ETag": "etag-1"}]},
        }
    ]
    assert fake_s3.aborted == []


def test_upload_stream_to_s3_aborts_on_failure():
    seed_models = _load_seed_models()
    fake_s3 = _FakeS3()

    def fail_upload_part(**kwargs):
        raise RuntimeError("upload failed")

    fake_s3.upload_part = fail_upload_part

    try:
        seed_models.upload_stream_to_s3(
            s3=fake_s3,
            fileobj=_FakeRemoteFile(),
            bucket="models-bucket",
            key="model.gguf",
            chunk_bytes=5,
            progress=lambda bytes_amount: None,
        )
    except RuntimeError as exc:
        assert str(exc) == "upload failed"
    else:
        raise AssertionError("expected upload failure")

    assert fake_s3.aborted == [
        {"Bucket": "models-bucket", "Key": "model.gguf", "UploadId": "upload-1"}
    ]


def test_upload_progress_reports_in_coarse_steps(capsys):
    seed_models = _load_seed_models()
    progress = seed_models.UploadProgress("model", step_bytes=10)

    progress(6)
    progress(4)
    progress(11)

    out = capsys.readouterr().out
    assert "model: streamed 0.0 GiB to S3" in out
    assert out.count("streamed") == 2
