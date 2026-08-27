from decimal import Decimal

from blogops.core.serialization import canonical_json_hash


def test_canonical_json_hash_is_order_independent_and_stringifies_unknown_values() -> None:
    assert canonical_json_hash({"한글": Decimal("1.25"), "enabled": True}) == canonical_json_hash(
        {"enabled": True, "한글": "1.25"}
    )
