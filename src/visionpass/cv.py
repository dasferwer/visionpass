import hashlib
import importlib
import math
from typing import Protocol

from visionpass.config import get_settings


class FaceEncodingError(ValueError):
    pass


class FaceEncoder(Protocol):
    def encode(self, image: bytes) -> list[float]: ...


class DeterministicDemoEncoder:
    """Local-only encoder used to test workflows without treating it as CV."""

    def encode(self, image: bytes) -> list[float]:
        if not image:
            raise FaceEncodingError("Image is empty")
        digest = hashlib.sha256(image).digest()
        return [value / 255 for value in digest[:16]]


class FaceRecognitionEncoder:
    """Adapter around ready-made OpenCV and face_recognition components."""

    def encode(self, image: bytes) -> list[float]:
        try:
            cv2 = importlib.import_module("cv2")
            numpy = importlib.import_module("numpy")
            face_recognition = importlib.import_module("face_recognition")
        except ImportError as exc:
            raise FaceEncodingError(
                "Install the cv extra to use the face_recognition backend"
            ) from exc
        matrix = cv2.imdecode(numpy.frombuffer(image, dtype=numpy.uint8), cv2.IMREAD_COLOR)
        if matrix is None:
            raise FaceEncodingError("Image cannot be decoded")
        rgb = cv2.cvtColor(matrix, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(rgb)
        if len(encodings) != 1:
            raise FaceEncodingError("Image must contain exactly one face")
        return [float(value) for value in encodings[0]]


def get_encoder() -> FaceEncoder:
    backend = get_settings().matcher_backend
    if backend == "stub":
        return DeterministicDemoEncoder()
    if backend == "face_recognition":
        return FaceRecognitionEncoder()
    raise RuntimeError(f"Unknown matcher backend: {backend}")


def euclidean_distance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise FaceEncodingError("Embedding dimensions differ")
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))
