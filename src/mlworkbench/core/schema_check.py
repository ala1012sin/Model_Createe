"""플러그인 parameter_schema 검사용 최소 JSON Schema 부분집합.

지원: type(object/integer/number/string/boolean/array), properties, required,
additionalProperties(false 기본), minimum/maximum, enum, const, items, minItems/maxItems,
uniqueItems, pattern. 외부 jsonschema 의존성을 두지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

_TYPE_CHECK = {
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def schema_error(schema: Any) -> str | None:
    """등록 전에 검사기가 해석할 스키마의 형태를 점검한다."""
    if not isinstance(schema, dict):
        return "파라미터 스키마는 객체여야 합니다"
    kind = schema.get("type")
    if kind is not None and (not isinstance(kind, str) or kind not in _TYPE_CHECK):
        return f"지원하지 않는 파라미터 타입입니다: {kind!r}"
    if "enum" in schema and not isinstance(schema["enum"], list):
        return "enum은 배열이어야 합니다"
    if "examples" in schema and not isinstance(schema["examples"], list):
        return "examples는 배열이어야 합니다"
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return "properties는 객체, required는 배열이어야 합니다"
        if any(not isinstance(key, str) or key not in properties for key in required):
            return "required에는 properties에 선언된 이름만 사용할 수 있습니다"
        for name, child in properties.items():
            if error := schema_error(child):
                return f"{name}: {error}"
    if kind == "array" and "items" in schema:
        return schema_error(schema["items"])
    for bound in ("minimum", "maximum", "minItems", "maxItems"):
        if bound in schema and not _TYPE_CHECK["number"](schema[bound]):
            return f"{bound}는 숫자여야 합니다"
    if kind == "string" and "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error):
            return "pattern은 유효한 정규식이어야 합니다"
    return None


def check(value: Any, schema: dict[str, Any], path: str = "", *, require_all: bool = True) -> list[tuple[str, str, str]]:
    """(path, code, message_ko) 목록 반환. require_all=False면 required 누락을 무시(초안)."""
    out: list[tuple[str, str, str]] = []
    t = schema.get("type")
    if t and not _TYPE_CHECK[t](value):
        return [(path, "PARAM_TYPE", f"{t} 타입이어야 합니다 (현재: {type(value).__name__})")]
    if "const" in schema and value != schema["const"]:
        out.append((path, "PARAM_CONST", f"이 값은 {schema['const']!r}로 고정입니다"))
    if "enum" in schema and value not in schema["enum"]:
        out.append((path, "PARAM_ENUM", f"허용값: {', '.join(map(str, schema['enum']))}"))
    if t in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            out.append((path, "PARAM_RANGE", f"{schema['minimum']} 이상이어야 합니다"))
        if "maximum" in schema and value > schema["maximum"]:
            out.append((path, "PARAM_RANGE", f"{schema['maximum']} 이하여야 합니다"))
    if t == "string" and "pattern" in schema and not re.fullmatch(schema["pattern"], value):
        example = (schema.get("examples") or ["?"])[0]
        out.append((path, "PARAM_PATTERN", f"형식이 올바르지 않습니다 (예: {example})"))
    if t == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append((path, "PARAM_RANGE", f"항목이 {schema['minItems']}개 이상이어야 합니다"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append((path, "PARAM_RANGE", f"항목이 {schema['maxItems']}개 이하여야 합니다"))
        if schema.get("uniqueItems") and len({repr(v) for v in value}) != len(value):
            out.append((path, "PARAM_DUPLICATE", "중복 항목이 있습니다"))
        if "items" in schema:
            for i, item in enumerate(value):
                out.extend(check(item, schema["items"], f"{path}[{i}]", require_all=require_all))
    if t == "object":
        props = schema.get("properties", {})
        if schema.get("additionalProperties", False) is False:
            for k in value:
                if k not in props:
                    out.append((f"{path}.{k}" if path else k, "PARAM_UNKNOWN", "정의되지 않은 파라미터입니다"))
        if require_all:
            for k in schema.get("required", []):
                if k not in value:
                    out.append((f"{path}.{k}" if path else k, "PARAM_REQUIRED", "필수 파라미터가 비어 있습니다"))
        for k, sub in props.items():
            if k in value:
                out.extend(check(value[k], sub, f"{path}.{k}" if path else k, require_all=require_all))
    return out
