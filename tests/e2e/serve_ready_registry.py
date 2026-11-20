"""테스트 전용 웹 서버: 플러그인 카탈로그를 골라 띄운다.

실행: python tests/e2e/serve_ready_registry.py <빈 워크스페이스> [포트, 기본 8001] [ready|preview|builtin, 기본 ready]
  ready   : 모든 내장 명세를 '실행 가능'으로 복제(구현 없음) — S4 승인 화면 흐름 확인용. 학습은 실행되지 않는다.
  preview : 구현 어댑터 없이 명세만(모두 미구현) — S2·S3·S4의 계획 미리보기·승인 차단 화면 확인용.
  builtin : 실제 내장 Registry(`mlwb serve`와 같음).
실제 내장 Registry·어댑터 등록 경로는 바꾸지 않는다.
"""

import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from test_preprocess import preview_registry, ready_registry  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402
from mlworkbench.core.registry import builtin_registry  # noqa: E402

if __name__ == "__main__":
    ws = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
    mode = sys.argv[3] if len(sys.argv) > 3 else "ready"
    registry = {"ready": ready_registry, "preview": preview_registry, "builtin": builtin_registry}[mode]()
    uvicorn.run(create_app(ws, registry=registry), host="127.0.0.1", port=port, log_level="warning")
