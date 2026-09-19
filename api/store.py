"""
Persistence for reports, jobs and the dead-letter queue.

Two implementations behind one small interface:
  FirestoreStore  - Cloud Run (STORE=firestore); collections: reports, jobs, dead_letter, settings
  MemoryStore     - local dev and unit tests (STORE=memory)

Idempotency key = sha256(email_id + subject + body + attachment names and content hashes).
The same email submitted twice maps to the same report document and is never processed twice.
"""
from __future__ import annotations
import hashlib, os, threading, time
from typing import Any, Optional

REPORTS, JOBS, DEAD_LETTER, SETTINGS = "reports", "jobs", "dead_letter", "settings"


def idempotency_key(email: dict) -> str:
    h = hashlib.sha256()
    h.update((email.get("email_id") or "").encode("utf-8", "replace")); h.update(b"\0")
    h.update((email.get("subject") or "").encode("utf-8", "replace")); h.update(b"\0")
    h.update((email.get("body") or "").encode("utf-8", "replace")); h.update(b"\0")
    for a in sorted(email.get("attachments") or [], key=lambda a: a.get("name", "")):
        h.update(a.get("name", "").encode("utf-8", "replace")); h.update(b"\0")
        content = a.get("content_base64") or a.get("text") or ""
        h.update(hashlib.sha256(content.encode("utf-8", "replace")).digest())
    return h.hexdigest()[:32]


def now() -> float:
    return time.time()


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()

    def get(self, col: str, key: str) -> Optional[dict]:
        with self._lock:
            d = self._data.get(col, {}).get(key)
            return dict(d) if d else None

    def set(self, col: str, key: str, doc: dict) -> None:
        with self._lock:
            self._data.setdefault(col, {})[key] = dict(doc)

    def update(self, col: str, key: str, patch: dict) -> None:
        with self._lock:
            self._data.setdefault(col, {}).setdefault(key, {}).update(patch)

    def list(self, col: str, where: Optional[tuple[str, str, Any]] = None, limit: int = 100) -> list[dict]:
        with self._lock:
            docs = [dict(d, id=k) for k, d in self._data.get(col, {}).items()]
        if where:
            f, _, v = where
            docs = [d for d in docs if d.get(f) == v]
        docs.sort(key=lambda d: d.get("updated", 0), reverse=True)
        return docs[:limit]


class FirestoreStore:
    def __init__(self, project: str, database: str = "(default)") -> None:
        from google.cloud import firestore
        self._db = firestore.Client(project=project, database=database)

    def get(self, col: str, key: str) -> Optional[dict]:
        snap = self._db.collection(col).document(key).get()
        return snap.to_dict() if snap.exists else None

    def set(self, col: str, key: str, doc: dict) -> None:
        self._db.collection(col).document(key).set(doc)

    def update(self, col: str, key: str, patch: dict) -> None:
        self._db.collection(col).document(key).set(patch, merge=True)

    def list(self, col: str, where: Optional[tuple[str, str, Any]] = None, limit: int = 100) -> list[dict]:
        from google.api_core.exceptions import FailedPrecondition
        from google.cloud.firestore_v1 import FieldFilter
        base = self._db.collection(col)
        if where:
            try:
                q = base.where(filter=FieldFilter(*where)).order_by("updated", direction="DESCENDING").limit(limit)
                return [dict(s.to_dict(), id=s.id) for s in q.stream()]
            except FailedPrecondition:
                # composite index (field + updated) not built yet: order only, filter in memory.
                # The index is created in docs/DEPLOY.md; this keeps the API usable meanwhile.
                f, _, v = where
                rows = [dict(s.to_dict(), id=s.id) for s in
                        base.order_by("updated", direction="DESCENDING").limit(limit * 10).stream()]
                return [r for r in rows if r.get(f) == v][:limit]
        q = base.order_by("updated", direction="DESCENDING").limit(limit)
        return [dict(s.to_dict(), id=s.id) for s in q.stream()]


def make_store():
    kind = (os.getenv("STORE") or "memory").strip().lower()
    if kind == "firestore":
        return FirestoreStore(os.environ["GCP_PROJECT"], os.getenv("FIRESTORE_DB", "(default)"))
    return MemoryStore()
