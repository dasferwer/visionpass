"""Запуск настоящего CV encoder; без БД, HTTP API и stub."""

import argparse
import importlib.metadata
import json
import platform
import time
from pathlib import Path

from visionpass.cv import FaceRecognitionEncoder
from visionpass.evaluation import evaluate, load_manifest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("root", type=Path)
parser.add_argument("--manifest", type=Path, default=Path("docs/cv-evaluation/manifest.json"))
parser.add_argument("--output", type=Path, default=Path("docs/cv-evaluation/result.json"))
args = parser.parse_args()
started = time.monotonic()
result = evaluate(load_manifest(args.manifest), args.root, FaceRecognitionEncoder())
result["environment"] = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "packages": {
        name: importlib.metadata.version(name)
        for name in (
            "face-recognition",
            "face-recognition-models",
            "dlib",
            "opencv-python-headless",
            "numpy",
        )
    },
}
result["elapsed_seconds"] = time.monotonic() - started
args.output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({key: value for key, value in result.items() if key != "trials"}, indent=2))
