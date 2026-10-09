"""Негативные контракты реального backend; вход — синтетическая single-face картинка."""

import io
import sys
from pathlib import Path

from PIL import Image

from visionpass.cv import FaceEncodingError, FaceRecognitionEncoder

encoder = FaceRecognitionEncoder()
source = Path(sys.argv[1]).read_bytes()
assert len(encoder.encode(source)) == 128
blank = io.BytesIO()
Image.new("RGB", (256, 256), "white").save(blank, format="PNG")
image = Image.open(io.BytesIO(source)).convert("RGB").resize((448, 448))
canvas = Image.new("RGB", (1000, 500), "white")
canvas.paste(image, (0, 0))
canvas.paste(image, (500, 0))
multiple = io.BytesIO()
canvas.save(multiple, format="PNG")
for name, data in (
    ("empty", b""),
    ("undecodable", b"invalid"),
    ("no-face", blank.getvalue()),
    ("multiple-faces", multiple.getvalue()),
):
    try:
        encoder.encode(data)
    except FaceEncodingError as exc:
        print(f"{name}: rejected ({exc})")
    else:
        raise AssertionError(f"{name}: should reject")
print("real CV: single face 128D; all 4 negative cases rejected")
