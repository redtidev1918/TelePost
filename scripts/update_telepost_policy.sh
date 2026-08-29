#!/usr/bin/env bash
set -euo pipefail

mode=${3:-}
if [[ $# -lt 2 || $# -gt 3 || ( $# -eq 3 && $mode != "--dry-run" ) ]]; then
  echo "Usage: $0 <fly-app> <policy.json> [--dry-run]" >&2
  exit 2
fi

app_name=$1
policy_file=$2
script_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
fly_config=${FLY_CONFIG:-$repo_dir/deploy.fly-multi-bot.toml}
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/telepost-policy.XXXXXX")
secrets_file=$work_dir/policy.env
trap 'rm -f "$secrets_file"; rmdir "$work_dir" 2>/dev/null || true' EXIT

python3 - "$policy_file" >"$secrets_file" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    policy = json.load(handle)

if not isinstance(policy, dict) or set(policy) != {"bots"}:
    raise SystemExit("policy root must contain only the 'bots' object")
bots = policy["bots"]
if not isinstance(bots, dict) or not bots:
    raise SystemExit("policy.bots must be a non-empty object")
for raw_index in bots:
    if not raw_index.isdigit() or int(raw_index) < 1:
        raise SystemExit(f"invalid bot index: {raw_index!r}")

mapping = {
    "channelId": "CHANNEL_ID",
    "reviewChatId": "REVIEW_CHAT_ID",
    "apiReviewRequired": "API_REVIEW_REQUIRED",
    "chatReviewRequired": "CHAT_REVIEW_REQUIRED",
}
lines = []
for raw_index, values in sorted(bots.items(), key=lambda item: int(item[0])):
    if not isinstance(values, dict) or not values:
        raise SystemExit(f"bot {raw_index} policy must be a non-empty object")
    unknown = set(values) - set(mapping)
    if unknown:
        raise SystemExit(f"bot {raw_index} has unknown keys: {sorted(unknown)}")
    for key, value in values.items():
        if key.endswith("Required"):
            if not isinstance(value, bool):
                raise SystemExit(f"bot {raw_index} {key} must be boolean")
            rendered = "true" if value else "false"
        else:
            if not isinstance(value, (str, int)) or not str(value).strip():
                raise SystemExit(f"bot {raw_index} {key} must be a non-empty chat id")
            rendered = str(value).strip()
        if "\n" in rendered or "\r" in rendered:
            raise SystemExit(f"bot {raw_index} {key} contains a newline")
        lines.append(f"BOT{int(raw_index)}_{mapping[key]}={rendered}")

print("\n".join(lines))
PY

if [[ $mode == "--dry-run" ]]; then
  echo "Policy is valid. Variables that would be updated:"
  cut -d= -f1 "$secrets_file"
  exit 0
fi

fly secrets import --stage -a "$app_name" -c "$fly_config" <"$secrets_file"
fly secrets deploy -a "$app_name" -c "$fly_config"

echo "TelePost policy deployed. Bot processes restarted once; persisted data was preserved."