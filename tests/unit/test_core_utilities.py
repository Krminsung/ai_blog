from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import Request

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import require_permission_value
from blogops.core.retries import capped_exponential_delay, deterministic_jittered_delay
from blogops.core.serialization import canonical_json_hash


def test_canonical_json_hash_is_order_independent_and_stringifies_unknown_values() -> None:
    assert canonical_json_hash({"한글": Decimal("1.25"), "enabled": True}) == canonical_json_hash(
        {"enabled": True, "한글": "1.25"}
    )


def test_permission_value_dependency_preserves_principal_and_custom_denial_message() -> None:
    request = Request({"type": "http"})
    principal = Principal(
        subject_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        permissions=frozenset({"platform:operate"}),
        authentication_method="password",
    )
    request.state.principal = principal

    assert require_permission_value("platform:operate")(request) is principal
    with pytest.raises(AppError) as denied:
        require_permission_value("billing:meter", message="사용량 확정 권한이 없습니다.")(
            request
        )
    assert denied.value.code == "PERMISSION_DENIED"
    assert denied.value.message == "사용량 확정 권한이 없습니다."


def test_retry_delays_are_capped_and_deterministic() -> None:
    assert capped_exponential_delay(base_seconds=5, maximum_seconds=300, exponent=99) == 300

    values = [
        deterministic_jittered_delay(
            base_seconds=10,
            maximum_seconds=60,
            jitter_ratio=0.2,
            attempt_no=2,
            seed="delivery-1",
        )
        for _ in range(2)
    ]
    assert values[0] == values[1]
    assert 16 <= values[0] <= 24
