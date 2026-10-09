"""Независимая оценка фиксированного CV matcher на синтетических изображениях."""

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from visionpass.cv import FaceEncoder, FaceEncodingError, euclidean_distance


def binomial_rate(errors: int, total: int) -> dict[str, Any]:
    if total < 0 or errors < 0 or errors > total:
        raise ValueError("Invalid binomial counts")
    if not total:
        return {"errors": errors, "total": total, "rate": None, "wilson95": None}
    proportion = errors / total
    z = 1.959963984540054
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total**2))
    radius /= denominator
    return {
        "errors": errors,
        "total": total,
        "rate": proportion,
        "wilson95": [max(0, center - radius), min(1, center + radius)],
    }


def evaluate(
    manifest: dict[str, Any], root: Path, encoder: FaceEncoder, threshold: float = 0.60
) -> dict[str, Any]:
    if encoder.model_id != "face-recognition-128-v1":
        raise ValueError("Evaluation requires the real FaceRecognitionEncoder")
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("Threshold must be finite and positive")
    images = manifest["images"]
    records: dict[tuple[str, int], list[float] | None] = {}
    rejections = []
    for item in images:
        key = (item["identity"], item["render"])
        if key in records:
            raise ValueError("Duplicate identity/render")
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Image path escapes root")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Image checksum mismatch")
        try:
            vector = encoder.encode(data)
            if len(vector) != 128 or not all(math.isfinite(value) for value in vector):
                raise ValueError("Invalid real-model embedding")
            records[key] = vector
        except FaceEncodingError as exc:
            records[key] = None
            rejections.append({"identity": key[0], "render": key[1], "reason": str(exc)})
    identities = sorted({key[0] for key in records}, key=int)
    if len(identities) < 2 or any(
        (identity, render) not in records for identity in identities for render in (0, 1, 18)
    ):
        raise ValueError("Need >=2 identities with enrollment 0 and probes 1,18")
    trials = []
    for claimed in identities:
        for probe_identity in identities:
            for render in (1, 18):
                enrollment = records[claimed, 0]
                probe = records[probe_identity, render]
                distance = (
                    euclidean_distance(enrollment, probe)
                    if enrollment is not None and probe is not None
                    else None
                )
                trials.append(
                    {
                        "claimed": claimed,
                        "probe": probe_identity,
                        "render": render,
                        "genuine": claimed == probe_identity,
                        "distance": distance,
                        "accepted": distance is not None and distance <= threshold,
                    }
                )
    genuine = [trial for trial in trials if trial["genuine"]]
    impostor = [trial for trial in trials if not trial["genuine"]]
    evaluable_genuine = [trial for trial in genuine if trial["distance"] is not None]
    evaluable_impostor = [trial for trial in impostor if trial["distance"] is not None]
    return {
        "model_id": encoder.model_id,
        "threshold": threshold,
        "threshold_policy": "pre-existing default; no tuning, training or calibration",
        "identities": len(identities),
        "images": len(records),
        "rejections": rejections,
        "image_rejection": binomial_rate(len(rejections), len(records)),
        "enrollment_rejection": binomial_rate(
            sum(records[identity, 0] is None for identity in identities), len(identities)
        ),
        "trial_rejection": binomial_rate(
            sum(trial["distance"] is None for trial in trials), len(trials)
        ),
        "far_all_attempts": binomial_rate(
            sum(bool(trial["accepted"]) for trial in impostor), len(impostor)
        ),
        "frr_all_attempts": binomial_rate(
            sum(not trial["accepted"] for trial in genuine), len(genuine)
        ),
        "far_encoded_only": binomial_rate(
            sum(bool(trial["accepted"]) for trial in evaluable_impostor), len(evaluable_impostor)
        ),
        "frr_encoded_only": binomial_rate(
            sum(not trial["accepted"] for trial in evaluable_genuine), len(evaluable_genuine)
        ),
        "interval_limit": "Wilson nominal 95%; repeated identities make trials dependent; "
        "not a population confidence guarantee",
        "trials": trials,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text()))
