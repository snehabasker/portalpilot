"""Credential vault with envelope encryption.

Each secret gets its own data key (DEK). The secret is encrypted with the DEK (Fernet); the DEK is encrypted
with a master key that never leaves its provider: a local Fernet key in development, AWS KMS in production.
Rotating the master key means re-wrapping DEKs, not re-encrypting every secret.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cryptography.fernet import Fernet

from .config import settings


@dataclass
class Sealed:
    secret_ct: bytes
    dek_ct: bytes
    provider: str
    key_id: str


class KeyProvider(Protocol):
    name: str
    key_id: str

    def new_dek(self) -> tuple[bytes, bytes]: ...       # (plaintext DEK, wrapped DEK)
    def unwrap(self, dek_ct: bytes) -> bytes: ...


class LocalKeyProvider:
    name = "local"

    def __init__(self, master_key: str):
        self._master = Fernet(master_key.encode())
        self.key_id = "local:" + master_key[:6]

    def new_dek(self) -> tuple[bytes, bytes]:
        dek = Fernet.generate_key()
        return dek, self._master.encrypt(dek)

    def unwrap(self, dek_ct: bytes) -> bytes:
        return self._master.decrypt(dek_ct)


class KmsKeyProvider:
    name = "kms"

    def __init__(self, key_id: str, region: str):
        import base64

        import boto3
        self._b64 = base64
        self._kms = boto3.client("kms", region_name=region)
        self.key_id = key_id

    def new_dek(self) -> tuple[bytes, bytes]:
        r = self._kms.generate_data_key(KeyId=self.key_id, KeySpec="AES_256")
        return self._b64.urlsafe_b64encode(r["Plaintext"]), r["CiphertextBlob"]

    def unwrap(self, dek_ct: bytes) -> bytes:
        return self._b64.urlsafe_b64encode(self._kms.decrypt(CiphertextBlob=dek_ct, KeyId=self.key_id)["Plaintext"])


def provider() -> KeyProvider:
    s = settings()
    if s.key_provider == "kms":
        if not s.kms_key_id:
            raise RuntimeError("KEY_PROVIDER=kms needs KMS_KEY_ID")
        return KmsKeyProvider(s.kms_key_id, s.aws_region)
    if not s.local_master_key:
        raise RuntimeError("KEY_PROVIDER=local needs LOCAL_MASTER_KEY (generate one with Fernet.generate_key())")
    return LocalKeyProvider(s.local_master_key)


def seal(secret: str, kp: KeyProvider | None = None) -> Sealed:
    kp = kp or provider()
    dek, dek_ct = kp.new_dek()
    return Sealed(Fernet(dek).encrypt(secret.encode()), dek_ct, kp.name, kp.key_id)


def unseal(secret_ct: bytes, dek_ct: bytes, kp: KeyProvider | None = None) -> str:
    kp = kp or provider()
    return Fernet(kp.unwrap(dek_ct)).decrypt(secret_ct).decode()
