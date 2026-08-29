#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <fly-app> <local-config.json>" >&2
  exit 2
fi

app_name=$1
local_config=$2
script_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
fly_config=${FLY_CONFIG:-$repo_dir/deploy.fly-multi-bot.toml}
remote_config=/app/data/pixivflow/config.json
remote_temp=/app/data/pixivflow/config.json.upload

python3 -m json.tool "$local_config" >/dev/null
fly ssh sftp put -a "$app_name" -c "$fly_config" "$local_config" "$remote_temp"
fly ssh console -a "$app_name" -c "$fly_config" -C "mv '$remote_temp' '$remote_config'"

echo "Uploaded atomically. PixivFlow will validate and hot-reload the file automatically."