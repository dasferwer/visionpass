"""Шифрование биометрических данных с привязкой к шаблону и поколению."""

import base64
import os
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from visionpass.config import get_settings


def cipher() -> AESGCM:
    return AESGCM(base64.b64decode(get_settings().encryption_key.get_secret_value(), validate=True))


def encrypt(data: bytes, template_id: UUID, generation: int, purpose: str) -> bytes:
    nonce = os.urandom(12)
    aad = f"visionpass:v1:{template_id}:{generation}:{purpose}".encode()
    return nonce + cipher().encrypt(nonce, data, aad)


def decrypt(data: bytes, template_id: UUID, generation: int, purpose: str) -> bytes:
    aad = f"visionpass:v1:{template_id}:{generation}:{purpose}".encode()
    return cipher().decrypt(data[:12], data[12:], aad)
