"""Метрики и границы входа; эти тесты не являются измерением качества CV."""

import hashlib
from pathlib import Path

import pytest

from visionpass.cv import DeterministicDemoEncoder, FaceEncodingError, FaceRecognitionEncoder
from visionpass.evaluation import binomial_rate, evaluate


class MetricFixtureEncoder:
    model_id = "face-recognition-128-v1"

    def encode(self, data: bytes) -> list[float]:
        if data == b"reject":
            raise FaceEncodingError("fixture rejection")
        return [int(data) / 10] + [0.0] * 127


def fixture_manifest(root: Path) -> dict:
    images = []
    for identity in (0, 1):
        for render in (0, 1, 18):
            data = b"reject" if (identity, render) == (1, 18) else str(identity).encode()
            path = f"{identity}-{render}.png"
            (root / path).write_bytes(data)
            images.append(
                {
                    "identity": str(identity),
                    "render": render,
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return {"images": images}


def test_denominators_include_rejections(tmp_path: Path) -> None:
    result = evaluate(fixture_manifest(tmp_path), tmp_path, MetricFixtureEncoder())
    assert result["frr_all_attempts"]["rate"] == 1 / 4
    assert result["far_all_attempts"]["rate"] == 3 / 4
    assert result["far_encoded_only"]["rate"] == 1
    assert result["frr_encoded_only"]["rate"] == 0
    assert result["trial_rejection"]["total"] == 8
    assert result["trial_rejection"]["errors"] == 2
    assert result["image_rejection"]["total"] == 6


def test_stub_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="real"):
        evaluate({}, tmp_path, DeterministicDemoEncoder())


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_invalid_threshold(tmp_path: Path, threshold: float) -> None:
    with pytest.raises(ValueError, match="Threshold"):
        evaluate({}, tmp_path, MetricFixtureEncoder(), threshold)


def test_changed_input_refused(tmp_path: Path) -> None:
    manifest = fixture_manifest(tmp_path)
    (tmp_path / manifest["images"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        evaluate(manifest, tmp_path, MetricFixtureEncoder())


def test_path_escape_refused(tmp_path: Path) -> None:
    manifest = fixture_manifest(tmp_path)
    manifest["images"][0]["path"] = "../outside.png"
    with pytest.raises(ValueError, match="escapes"):
        evaluate(manifest, tmp_path, MetricFixtureEncoder())


def test_duplicate_refused(tmp_path: Path) -> None:
    manifest = fixture_manifest(tmp_path)
    manifest["images"].append(manifest["images"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate(manifest, tmp_path, MetricFixtureEncoder())


def test_zero_and_small_sample_uncertainty() -> None:
    assert binomial_rate(0, 0)["rate"] is None
    assert binomial_rate(0, 4)["wilson95"][1] > 0.48
    with pytest.raises(ValueError):
        binomial_rate(5, 4)


def test_real_encoder_rejects_empty_before_decode() -> None:
    with pytest.raises(FaceEncodingError, match="empty"):
        FaceRecognitionEncoder().encode(b"")
