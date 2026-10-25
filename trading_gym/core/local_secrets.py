"""Encrypted local connector credentials; API responses contain IDs only."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from filelock import FileLock

from trading_gym.core.persistence import atomic_write_json, read_json


def _windows_protect(data: bytes, decrypt=False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    operation = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not operation(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)
    ):
        raise OSError("Windows credential protection failed")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


class LocalSecrets:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / "credentials.json"
        self.lock = FileLock(str(self.home / "credentials.lock"))

    def _cipher(self):
        path = self.home / "master.json"
        record = read_json(path, {})
        if not record:
            key = Fernet.generate_key()
            payload = _windows_protect(key) if os.name == "nt" else key
            record = {
                "protection": "windows_dpapi" if os.name == "nt" else "private_file",
                "key": base64.b64encode(payload).decode(),
            }
            atomic_write_json(path, record)
        payload = base64.b64decode(record["key"])
        key = (
            _windows_protect(payload, decrypt=True)
            if record["protection"] == "windows_dpapi"
            else payload
        )
        return Fernet(key)

    def read(self):
        with self.lock:
            record = read_json(self.path, {})
            if not record:
                return {"abantether": "", "llm": [], "decision1": []}
            return json.loads(self._cipher().decrypt(record["ciphertext"].encode()))

    def update(self, *, abantether=None, llm=None, decision1=None):
        with self.lock:
            record = self.read()
            for name, value in [
                ("abantether", abantether),
                ("llm", llm),
                ("decision1", decision1),
            ]:
                if value is None:
                    continue
                values = (
                    [value]
                    if isinstance(value, str) and value
                    else [] if value == "" else value
                )
                if len(values) > 16 or any(
                    not isinstance(k, str)
                    or not k.strip()
                    or k != k.strip()
                    or len(k) > 4096
                    or "\n" in k
                    or "\r" in k
                    for k in values
                ):
                    raise ValueError(
                        "Provide at most 16 non-empty single-line keys per gateway"
                    )
                if len(set(values)) != len(values):
                    raise ValueError("Duplicate API keys are not allowed")
                record[name] = value
            ciphertext = self._cipher().encrypt(json.dumps(record).encode()).decode()
            atomic_write_json(self.path, {"ciphertext": ciphertext})
        return self.summary()

    def key(self, role):
        return (
            self.read()["abantether"] or os.getenv("ABAN_API_KEY", "")
            if role == "abantether"
            else ""
        )

    def mapping(self, role):
        values = self.read().get(role, [])
        if not values and os.getenv(
            "LLM_API_KEY" if role == "llm" else "DECISION1_API_KEY"
        ):
            values = [
                os.environ["LLM_API_KEY" if role == "llm" else "DECISION1_API_KEY"]
            ]
        return {
            f"KEY_{hashlib.sha256(v.encode()).hexdigest()[:24].upper()}": v
            for v in values
        }

    def summary(self):
        return {
            "abantether_configured": bool(self.key("abantether")),
            "storage": (
                "Windows DPAPI" if os.name == "nt" else "encrypted private local file"
            ),
            "llm": [
                {"id": k, "label": f"Key {i+1}"}
                for i, k in enumerate(self.mapping("llm"))
            ],
            "decision1": [
                {"id": k, "label": f"Key {i+1}"}
                for i, k in enumerate(self.mapping("decision1"))
            ],
        }
