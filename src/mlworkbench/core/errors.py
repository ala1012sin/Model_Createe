"""도메인 오류. message_ko는 화면에 그대로 보여줄 수 있는 한글 설명."""


class WorkbenchError(Exception):
    code = "WORKBENCH_ERROR"

    def __init__(self, message_ko: str, *, code: str | None = None):
        super().__init__(message_ko)
        self.message_ko = message_ko
        if code:
            self.code = code


class RegistryError(WorkbenchError):
    code = "REGISTRY_ERROR"


class ConfigError(WorkbenchError):
    code = "CONFIG_ERROR"


class PolicyError(WorkbenchError):
    """데이터 전송 정책 위반."""

    code = "POLICY_BLOCKED"


class ProviderError(WorkbenchError):
    code = "PROVIDER_ERROR"


class ProviderOutputError(ProviderError):
    code = "PROVIDER_OUTPUT_INVALID"

    def __init__(self, message_ko: str, issues: list | None = None):
        super().__init__(message_ko)
        self.issues = issues or []


class ApprovalError(WorkbenchError):
    code = "APPROVAL_REJECTED"


class SubmitRejected(WorkbenchError):
    code = "SUBMIT_REJECTED"


class ToolDenied(WorkbenchError):
    code = "TOOL_DENIED"


class StateTransitionError(WorkbenchError):
    code = "INVALID_TRANSITION"


class NotFound(WorkbenchError):
    code = "NOT_FOUND"
