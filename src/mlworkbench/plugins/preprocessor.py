"""신뢰된 로컬 전처리 구현체의 계약. 호출 및 데이터 분할은 실행 경로가 맡는다."""

from __future__ import annotations

from typing import Any, Protocol

from mlworkbench.core.contracts import DatasetManifest, PluginSpec


class PreprocessorAdapter(Protocol):
    spec: PluginSpec

    def validate(self, dataset: DatasetManifest | None, params: dict[str, Any]) -> list[str]:
        """데이터나 파라미터의 추가 제약을 검사하고 오류를 반환한다."""
        ...

    def fit(self, train_data: Any, params: dict[str, Any]) -> Any:
        """학습 분할에서만 상태를 얻는다. 무상태 연산은 None을 반환할 수 있다."""
        ...

    def transform(self, data: Any, state: Any, params: dict[str, Any]) -> Any: ...

    def preview(self, sample: Any, params: dict[str, Any]) -> Any: ...
