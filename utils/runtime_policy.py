"""持久化非敏感 Bot 运行策略。"""
import json
import os
import tempfile


POLICY_KEYS = {
    "CHANNEL_ID",
    "REVIEW_CHAT_ID",
    "API_REVIEW_REQUIRED",
    "CHAT_REVIEW_REQUIRED",
    "SHOW_SUBMITTER",
}
BOOL_KEYS = {
    "API_REVIEW_REQUIRED",
    "CHAT_REVIEW_REQUIRED",
    "SHOW_SUBMITTER",
}


def validate_runtime_policy(policy: dict) -> dict[str, str]:
    if not isinstance(policy, dict):
        raise ValueError("runtime policy must be a JSON object")
    unknown = set(policy) - POLICY_KEYS
    if unknown:
        raise ValueError(f"unknown runtime policy keys: {sorted(unknown)}")

    normalized = {}
    for key, value in policy.items():
        if key in BOOL_KEYS:
            if isinstance(value, bool):
                normalized[key] = "true" if value else "false"
            elif str(value).strip().lower() in {"true", "false"}:
                normalized[key] = str(value).strip().lower()
            else:
                raise ValueError(f"{key} must be true or false")
        else:
            rendered = str(value).strip()
            if not rendered or "\n" in rendered or "\r" in rendered:
                raise ValueError(f"{key} must be a non-empty chat id")
            normalized[key] = rendered
    return normalized


def load_runtime_policy(path: str) -> dict[str, str]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return validate_runtime_policy(json.load(handle))


def update_runtime_policy(path: str, changes: dict) -> dict[str, str]:
    policy = load_runtime_policy(path)
    policy.update(changes)
    policy = validate_runtime_policy(policy)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".runtime-policy-", dir=directory, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(policy, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return policy


def clear_runtime_policy(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
