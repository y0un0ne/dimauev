from __future__ import annotations

import ctypes
import json
import os
import re
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


APP_NAME = "DimensionsAutoEvaluate"
ALLOWED_CREDENTIALS = {
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "OPENROUTER_API_KEY",
}

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
ACCOUNT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
METADATA_ROOT = Path(os.getenv("LOCALAPPDATA", ".")) / "DimensionsAutoEvaluate"
GEMINI_ACCOUNTS_FILE = METADATA_ROOT / "gemini_accounts.json"


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


class CredentialStore:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager is available only on Windows")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredWriteW.argtypes = [PCREDENTIALW, wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(PCREDENTIALW),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    @staticmethod
    def _validate(name: str) -> None:
        if name not in ALLOWED_CREDENTIALS:
            raise ValueError(f"Unsupported credential: {name}")

    @staticmethod
    def _target(name: str) -> str:
        return f"{APP_NAME}/{name}"

    @staticmethod
    def _account_target(account_id: str) -> str:
        if not ACCOUNT_ID_RE.fullmatch(account_id):
            raise ValueError("Invalid Gemini account ID")
        return f"{APP_NAME}/Gemini/{account_id}/key"

    @staticmethod
    def _read_accounts() -> list[Dict[str, object]]:
        if not GEMINI_ACCOUNTS_FILE.exists():
            return []
        try:
            value = json.loads(GEMINI_ACCOUNTS_FILE.read_text(encoding="utf-8"))
            accounts = value.get("accounts", []) if isinstance(value, dict) else []
            return [item for item in accounts if isinstance(item, dict)]
        except Exception:
            return []

    @staticmethod
    def _write_accounts(accounts: list[Dict[str, object]]) -> None:
        METADATA_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = GEMINI_ACCOUNTS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"accounts": accounts}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(GEMINI_ACCOUNTS_FILE)

    def _set_target(self, target: str, value: str, comment: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("Credential value cannot be empty")
        raw = value.encode("utf-16-le")
        if len(raw) > 2560:
            raise ValueError("Credential value exceeds Windows Credential Manager limit")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = comment
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = APP_NAME
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def _get_target(self, target: str) -> Optional[str]:
        pointer = PCREDENTIALW()
        if not self._advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not credential.CredentialBlobSize:
                return ""
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            self._advapi.CredFree(pointer)

    def _delete_target(self, target: str) -> bool:
        if self._advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return False
        raise ctypes.WinError(error)

    def set(self, name: str, value: str) -> None:
        self._validate(name)
        self._set_target(self._target(name), value, f"Stored by {APP_NAME} local application")

    def get(self, name: str) -> Optional[str]:
        self._validate(name)
        return self._get_target(self._target(name))

    def delete(self, name: str) -> bool:
        self._validate(name)
        return self._delete_target(self._target(name))

    def list_gemini_accounts(self) -> list[Dict[str, object]]:
        accounts = self._read_accounts()
        # One-time migration of the original single saved Gemini credential.
        legacy = self.get("GEMINI_API_KEY")
        if not accounts and legacy:
            account_id = uuid.uuid4().hex
            self._set_target(
                self._account_target(account_id), legacy,
                f"Stored by {APP_NAME} Gemini account",
            )
            self._delete_target(self._target("GEMINI_API_KEY"))
            accounts = [{
                "id": account_id,
                "label": "Migrated default account",
                "email": "",
                "enabled": True,
                "priority": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }]
            self._write_accounts(accounts)
        normalized = []
        for item in accounts:
            account_id = str(item.get("id") or "")
            if not ACCOUNT_ID_RE.fullmatch(account_id):
                continue
            normalized.append({
                "id": account_id,
                "label": str(item.get("label") or "Unnamed account"),
                "email": str(item.get("email") or ""),
                "enabled": bool(item.get("enabled", True)),
                "priority": int(item.get("priority") or 999),
                "configured": bool(self._get_target(self._account_target(account_id))),
            })
        return sorted(normalized, key=lambda item: (int(item["priority"]), str(item["label"]).lower()))

    def add_gemini_account(self, label: str, email: str, key: str) -> Dict[str, object]:
        label = label.strip()
        email = email.strip()
        if not label:
            raise ValueError("Gemini account label is required")
        if len(label) > 100 or len(email) > 254:
            raise ValueError("Gemini account label or email is too long")
        accounts = self._read_accounts()
        account_id = uuid.uuid4().hex
        priority = max((int(item.get("priority") or 0) for item in accounts), default=0) + 1
        self._set_target(self._account_target(account_id), key, f"Stored by {APP_NAME} Gemini account")
        accounts.append({
            "id": account_id,
            "label": label,
            "email": email,
            "enabled": True,
            "priority": priority,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._write_accounts(accounts)
        return next(item for item in self.list_gemini_accounts() if item["id"] == account_id)

    def delete_gemini_account(self, account_id: str) -> bool:
        target = self._account_target(account_id)
        accounts = self._read_accounts()
        if not any(str(item.get("id")) == account_id for item in accounts):
            return False
        self._delete_target(target)
        self._write_accounts([item for item in accounts if str(item.get("id")) != account_id])
        return True

    def gemini_environment(self) -> Dict[str, str]:
        accounts = [item for item in self.list_gemini_accounts() if item["enabled"] and item["configured"]]
        values: Dict[str, str] = {"GEMINI_ACCOUNT_COUNT": str(len(accounts))}
        for index, account in enumerate(accounts, start=1):
            key = self._get_target(self._account_target(str(account["id"])))
            if not key:
                continue
            values[f"GEMINI_API_KEY_{index}"] = key
            values[f"GEMINI_ACCOUNT_ID_{index}"] = str(account["id"])
            values[f"GEMINI_ACCOUNT_LABEL_{index}"] = str(account["label"])
        return values

    def configured(self) -> Dict[str, bool]:
        configured = {name: bool(self.get(name) or os.getenv(name)) for name in sorted(ALLOWED_CREDENTIALS)}
        configured["GEMINI_API_KEY"] = bool(
            any(item["configured"] and item["enabled"] for item in self.list_gemini_accounts())
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        return configured

    def environment(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
        for name in ALLOWED_CREDENTIALS:
            value = self.get(name)
            if value:
                values[name] = value
        values.update(self.gemini_environment())
        # A numbered pool takes precedence in evaluator processes.
        if values.get("GEMINI_ACCOUNT_COUNT") != "0":
            values.pop("GEMINI_API_KEY", None)
        return values
