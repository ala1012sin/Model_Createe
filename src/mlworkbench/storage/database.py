"""SQLite + 로컬 아티팩트 저장소 초기화.

워크스페이스 구조 (ARCHITECTURE §8):
  workspace/datasets/  workspace/plans/<plan_id>/  workspace/experiments/<run_id>/  workspace/mlwb.sqlite
API 키·원시 데이터는 저장하지 않는다. 승인 토큰은 해시만 저장한다.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datasets (
  dataset_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (
  draft_id TEXT NOT NULL, revision INTEGER NOT NULL, body_json TEXT NOT NULL,
  exec_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (draft_id, revision));
CREATE TABLE IF NOT EXISTS approvals (
  approval_ref TEXT PRIMARY KEY,          -- approval_id의 해시. 원문은 저장하지 않음
  plan_id TEXT NOT NULL UNIQUE, plan_hash TEXT NOT NULL,
  draft_id TEXT NOT NULL, draft_revision INTEGER NOT NULL,
  dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','invalidated','consumed')),
  snapshot_json TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, approval_ref TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT NOT NULL, PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS confirmations (
  confirmation_ref TEXT PRIMARY KEY, run_id TEXT NOT NULL, action TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS profiles (
  dataset_id TEXT PRIMARY KEY, profile_version INTEGER NOT NULL, dataset_version TEXT NOT NULL,
  profile_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL,
  profile_version INTEGER NOT NULL, mode TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS presets (
  task TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, preset_json TEXT NOT NULL,
  PRIMARY KEY (task, name, version));
CREATE TABLE IF NOT EXISTS applications (   -- S7 새 데이터 적용(저장 모델로 추론)
  application_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, plan_hash TEXT NOT NULL,
  state TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

DB_SCHEMA_VERSION = "1"


class Workspace:
    def __init__(self, root: str | os.PathLike | None = None):
        root = root or os.environ.get("MLWB_WORKSPACE") or "workspace"
        self.root = Path(root).resolve()
        self.db_path = self.root / "mlwb.sqlite"

    def init(self) -> "Workspace":
        for sub in ("datasets", "plans", "experiments"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        with self.session() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('db_schema_version', ?)", (DB_SCHEMA_VERSION,))
        return self

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)  # autocommit; 트랜잭션은 명시
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # 저널 파일을 삭제하지 않고 비우는 방식. 동기화 폴더·네트워크/가상 드라이브처럼
        # 파일 삭제가 제한된 위치에서도 커밋이 실패하지 않게 한다(기본 DELETE 방식은 disk I/O error).
        conn.execute("PRAGMA journal_mode = TRUNCATE")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """사용 후 반드시 닫는 연결 (Windows 파일 잠금 방지)."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def plan_dir(self, plan_id: str) -> Path:
        p = self.root / "plans" / plan_id
        p.mkdir(parents=True, exist_ok=True)
        return p
