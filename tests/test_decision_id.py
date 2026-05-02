# / decision_id ulid utility

from src.agents.decision_id import new_decision_id


def test_returns_26_char_string():
    did = new_decision_id()
    assert isinstance(did, str)
    assert len(did) == 26


def test_unique_across_calls():
    ids = {new_decision_id() for _ in range(100)}
    assert len(ids) == 100


def test_uppercase_crockford_base32():
    did = new_decision_id()
    valid = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert all(c in valid for c in did)
