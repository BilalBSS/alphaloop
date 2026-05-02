from ulid import ULID


def new_decision_id() -> str:
    return str(ULID())
