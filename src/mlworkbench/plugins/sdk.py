"""사용자 정의 시계열 전처리 플러그인을 쓸 때 쓰는 작은 도구 모음.

플러그인 클래스는 아래 메서드를 가진다(`UserFeature`를 상속하면 기본 동작이 채워진다).

- transform(frame, state, params) -> TsFrame   (필수) 새 피처 열을 추가한 사본을 돌려준다.
- fit(train_frame, params) -> JSON 저장 가능한 값 (fit_scope="train_only"일 때만 호출)
- validate(dataset, params) -> list[str]       데이터·파라미터 추가 검사(오류 문구 목록)
- history(params, target) -> (칸 수, {열: 설명}) S7 입력 안내용 과거 이력 요구

규칙(실행 시 자동 검사, 어기면 실행 실패):
- 라벨(미래 정답) 열은 보이지 않는다. 기준 시각까지의 관측만 쓴다(앞부분만 잘라 다시 계산해 같은지 확인).
- 행 수·순서, 시간·설비·세션, 기존 열과 피처는 바꾸지 않는다. 새 피처는 `add_feature`로만 추가한다.
"""

from __future__ import annotations

from typing import Any

from mlworkbench.pipelines.ts_frame import GROUP, SESSION, TIME, PipelineError, TsFrame

__all__ = ["GROUP", "SESSION", "TIME", "PipelineError", "TsFrame", "UserFeature", "add_feature", "group_keys"]


def group_keys(frame: TsFrame) -> list:
    """같은 설비·세션끼리 묶는 groupby 키. 이 묶음 안에서만 과거 값을 써야 경계를 넘지 않는다."""
    return [frame.df[GROUP], frame.df[SESSION]]


def add_feature(frame: TsFrame, name: str, values: Any) -> None:
    """새 입력 피처 열을 추가하고 피처 목록에 등록한다."""
    frame.df[name] = values
    frame.feature_cols.append(name)


class UserFeature:
    """사용자 플러그인 기본 클래스(상속은 선택)."""

    def validate(self, dataset: Any, params: dict[str, Any]) -> list[str]:
        return []

    def fit(self, train_frame: TsFrame, params: dict[str, Any]) -> Any:
        return None

    def history(self, params: dict[str, Any], target: str) -> tuple[int, dict[str, str]]:
        return 0, {}

    def transform(self, frame: TsFrame, state: Any, params: dict[str, Any]) -> TsFrame:  # pragma: no cover
        raise NotImplementedError
