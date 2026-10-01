"""Bounded SSH inventory and deployment helper for the standalone runtime.

The password is read from an environment variable and is never accepted as a
command-line argument. The archive includes only the files needed to build
the independent Crypto runtime plus verified CSV/Manifest history.
"""

from __future__ import annotations

import argparse
import io
from ipaddress import IPv4Address
import json
import os
import shlex
import tarfile
import time
import uuid
from pathlib import Path

import paramiko


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_DIR = "~/crypto-platform"
DEFAULT_BIND_ADDRESS = "10.10.10.129"
DEV_ENV_SOURCE = ROOT / "backend" / ".env.example"
EXCLUDED_NAMES = {
    ".git",
    ".playwright-cli",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}
SOURCE_FILES = (
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "Dockerfile.overlay",
    "compose.yaml",
    "pyproject.toml",
    "README.md",
    "backend",
    "contracts",
    "docs",
    "frontend",
    "scripts",
    "src",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory or deploy crypto-platform over SSH")
    parser.add_argument("--host", default="10.10.10.129")
    parser.add_argument("--username", default="flybace")
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    parser.add_argument(
        "--key-file",
        default=os.getenv("CRYPTO_REMOTE_KEY", ""),
        help="SSH private key path; avoids placing the remote password in a command",
    )
    parser.add_argument(
        "--bind-address",
        default=DEFAULT_BIND_ADDRESS,
        help="IPv4 address published by the remote Compose runtime",
    )
    parser.add_argument("--password-env", default="CRYPTO_REMOTE_PASSWORD")
    parser.add_argument(
        "action",
        choices=("inventory", "diagnose", "postflight", "deploy", "hotfix", "frontend-hotfix", "archive"),
    )
    parser.add_argument("--skip-history", action="store_true")
    parser.add_argument(
        "--bootstrap-dev-env",
        action="store_true",
        help="upload backend/.env.example only when the remote .env is absent",
    )
    args = parser.parse_args()
    try:
        args.bind_address = str(IPv4Address(args.bind_address))
    except ValueError as error:
        parser.error(f"--bind-address must be an IPv4 address: {error}")
    return args


def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    password = os.environ.get(args.password_env, "")
    key_file = Path(args.key_file).expanduser() if args.key_file else None
    if key_file is not None and not key_file.is_file():
        raise SystemExit(f"SSH key file does not exist: {key_file}")
    if not password and key_file is None:
        raise SystemExit(
            f"set {args.password_env} or pass --key-file/CRYPTO_REMOTE_KEY for SSH authentication"
        )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        args.host,
        username=args.username,
        password=password or None,
        key_filename=str(key_file) if key_file is not None else None,
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
    )
    return client


def run(client: paramiko.SSHClient, command: str, *, timeout: int = 30) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    channel = stdout.channel
    output = bytearray()
    error_output = bytearray()
    deadline = time.monotonic() + timeout
    while not channel.exit_status_ready():
        while channel.recv_ready():
            output.extend(channel.recv(65536))
        while channel.recv_stderr_ready():
            error_output.extend(channel.recv_stderr(65536))
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError(f"remote command exceeded {timeout}s")
        time.sleep(0.01)
    while channel.recv_ready():
        output.extend(channel.recv(65536))
    while channel.recv_stderr_ready():
        error_output.extend(channel.recv_stderr(65536))
    exit_code = channel.recv_exit_status()
    return (
        exit_code,
        output.decode("utf-8", errors="replace"),
        error_output.decode("utf-8", errors="replace"),
    )


def inventory(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    remote_dir = _remote_dir_expression(args.remote_dir)
    command = f"""set -u
hostname
uname -a
printf 'HOME=%s\\n' "$HOME"
pwd
printf '%s\\n' '--- home ---'
ls -la
printf '%s\\n' '--- docker ---'
docker ps --format 'table {{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}' 2>/dev/null || true
docker compose ls 2>/dev/null || true
printf '%s\\n' '--- listeners ---'
ss -ltn 2>/dev/null | head -40 || true
printf '%s\\n' '--- crypto project ---'
if [ -d {remote_dir} ]; then
  cd {remote_dir}
  printf 'PROJECT_DIR=%s\\n' "$PWD"
  printf 'HISTORY_CSV='; find data/history -type f -name '*.csv' 2>/dev/null | wc -l || true
  printf 'HISTORY_MANIFEST='; find data/history -type f -name '*.manifest.json' 2>/dev/null | wc -l || true
  if [ -f .env ]; then
    printf 'REMOTE_DOTENV=present\\n'
    for key in CRYPTO_ADMIN_PASSWORD CRYPTO_SESSION_SECRET; do
      if awk -F= -v wanted="$key" '$1 == wanted && length($0) > length(wanted) + 1 {{ found=1; exit }} END {{ exit(found ? 0 : 1) }}' .env; then
        printf '%s=present\\n' "$key"
      else
        printf '%s=missing\\n' "$key"
      fi
    done
  else
    printf 'REMOTE_DOTENV=missing\\n'
    printf 'CRYPTO_ADMIN_PASSWORD=unknown\\nCRYPTO_SESSION_SECRET=unknown\\n'
  fi
  docker compose -p crypto-platform config --services 2>/dev/null || true
  docker compose -p crypto-platform ps --all 2>/dev/null || true
fi
"""
    code, output, error = run(client, command)
    print(output, end="")
    print(error, end="")
    return code


def postflight(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    expected_dataset_count, expected_row_count = _local_history_expectations()
    remote_dir = _remote_dir_expression(args.remote_dir)
    command = f"""set -eu
REMOTE_DIR={remote_dir}
cd "$REMOTE_DIR"
printf '%s\\n' '--- crypto compose ---'
docker compose -p crypto-platform ps --all
test "$(docker compose -p crypto-platform port backend 8000)" = "{args.bind_address}:8290"
test "$(docker compose -p crypto-platform port frontend 8080)" = "{args.bind_address}:4191"
printf 'CRYPTO_BIND_ADDRESS={args.bind_address}\\n'
for service in task-worker task-scheduler; do
  container_id=$(docker compose -p crypto-platform ps -q "$service")
  test -n "$container_id"
  test "$(docker inspect -f '{{{{.State.Running}}}}' "$container_id")" = true
done
printf 'QUANT_RUNNING='
docker compose -p quant-platform ps --status running -q 2>/dev/null | wc -l || true
printf '%s\\n' '--- backend authenticated postflight ---'
docker compose -p crypto-platform exec -T backend python - <<'PY'
import json
import os
from urllib.parse import urlencode
import urllib.request

EXPECTED_DATASET_COUNT = {expected_dataset_count}
EXPECTED_ROW_COUNT = {expected_row_count}


def request(path, method="GET", payload=None, token=None, timeout=30):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {{"Content-Type": "application/json"}}
    if token:
        headers["Authorization"] = f"Bearer {{token}}"
    request = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


health_status, health = request("/health")
login_status, login = request(
    "/api/v1/auth/login",
    method="POST",
    payload={{
        "username": os.environ.get("CRYPTO_ADMIN_USERNAME", "admin"),
        "password": os.environ["CRYPTO_ADMIN_PASSWORD"],
    }},
)
coverage_status, coverage = request(
    "/api/v1/history/coverage",
    token=login["access_token"],
)
scheduler_status, scheduler = request(
    "/api/v1/history/sync/status",
    token=login["access_token"],
)
archive_status, archive = request(
    "/api/v1/history/archive",
    token=login["access_token"],
)
datasets = coverage.get("datasets", [])
metadata = coverage.get("metadata", {{}})


def dataset_for(venue_id):
    return next(
        item
        for item in datasets
        if item.get("venue_id") == venue_id
        and item.get("instrument_key") == f"{{venue_id}}:spot:BTC/USDT"
        and item.get("interval") == "5m"
    )


buy_dataset = dataset_for("binance")
sell_dataset = dataset_for("bybit")
spread_start = max(str(buy_dataset["start_at"]), str(sell_dataset["start_at"]))
spread_end = min(str(buy_dataset["end_at"]), str(sell_dataset["end_at"]))
assert spread_end > spread_start
spread_query = urlencode({{
    "symbol": "BTC/USDT",
    "buy_venue_id": "binance",
    "sell_venue_id": "bybit",
    "interval": "5m",
    "fee_bps": "10",
    "slippage_bps": "5",
    "min_net_spread_bps": "0",
    "start_at": spread_start,
    "end_at": spread_end,
    "limit": "1",
}})
spread_status, spread = request(
    "/api/v1/market/spread-history?" + spread_query,
    token=login["access_token"],
    timeout=60,
)
task_status, task_summary = request(
    "/api/v1/tasks/summary",
    token=login["access_token"],
)
assert health_status == 200 and health.get("execution_mode") == "DISABLED"
assert login_status == 200
assert coverage_status == 200
assert scheduler_status == 200
assert scheduler.get("automatic") is True
assert int(scheduler.get("interval_seconds", 0)) > 0
assert int(coverage.get("dataset_count", 0)) >= EXPECTED_DATASET_COUNT
assert int(coverage.get("row_count", 0)) >= EXPECTED_ROW_COUNT
assert all(item.get("gap_count") == 0 and item.get("duplicate_count") == 0 for item in datasets)
assert metadata.get("ok") is True
assert int(metadata.get("dataset_count", 0)) >= EXPECTED_DATASET_COUNT
assert archive_status == 200
assert archive.get("status") == "READY"
assert int(archive.get("archived_count", 0)) >= EXPECTED_DATASET_COUNT
assert archive.get("missing_count") == 0
assert spread_status == 200
assert spread.get("research_only") is True
assert spread.get("interval") == "5m"
assert int(spread.get("aligned_candle_count", 0)) > 0
assert spread.get("cost_model", {{}}).get("total_cost_bps") == "30"
assert task_status == 200
assert task_summary.get("persistence", {{}}).get("ok") is True
print("BACKEND_HEALTH=200")
print("BACKEND_EXECUTION_MODE=" + str(health.get("execution_mode")))
print("AUTH_LOGIN=200")
print("HISTORY_DATASETS=" + str(coverage.get("dataset_count")))
print("HISTORY_DATASETS_EXPECTED_MIN=" + str(EXPECTED_DATASET_COUNT))
print("HISTORY_ROWS=" + str(coverage.get("row_count")))
print("HISTORY_QUALITY=gap0_duplicate0")
print("HISTORY_SCHEDULER=" + str(scheduler.get("status")))
print("HISTORY_SCHEDULER_INTERVAL_SECONDS=" + str(scheduler.get("interval_seconds")))
print("HISTORY_METADATA=" + str(metadata.get("dataset_count")) + "/" + str(coverage.get("dataset_count")))
print("HISTORY_PARQUET=" + str(archive.get("archived_count")) + "/" + str(archive.get("dataset_count")))
print("HISTORICAL_SPREAD_STATUS=" + str(spread_status))
print("HISTORICAL_SPREAD_ALIGNED=" + str(spread.get("aligned_candle_count")))
print("HISTORICAL_SPREAD_COST_BPS=" + str(spread.get("cost_model", {{}}).get("total_cost_bps")))
print("TASK_LEDGER=" + str(task_summary.get("total")))
print("TASK_PERSISTENCE=ready")
PY
printf '%s\\n' '--- public market postflight ---'
market_worker_id=$(docker compose -p crypto-platform ps -q market-worker 2>/dev/null || true)
if [ -n "$market_worker_id" ]; then
  test "$(docker inspect -f '{{{{.State.Running}}}}' "$market_worker_id")" = true
  market_health=$(docker inspect -f '{{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{end}}}}' "$market_worker_id")
  test "$market_health" = healthy
  printf 'MARKET_WORKER=running\\n'
  printf 'MARKET_WORKER_HEALTH=%s\\n' "$market_health"
  docker compose -p crypto-platform exec -T market-worker python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

root = Path("/runtime/market")
entries = [item.strip() for item in os.environ.get("CRYPTO_MARKET_INSTRUMENTS", "").split(",") if item.strip()]
assert os.environ.get("CRYPTO_MARKET_WORKER_ENABLED", "false").strip().lower() == "true"
assert entries

for entry in entries:
    venue, native_symbol = entry.split(":", 1)
    status = json.loads((root / f"status-{{venue.lower()}}.json").read_text())
    assert status.get("state") == "CONNECTED", entry
    snapshot = None
    for path in root.glob("snapshot-*.json"):
        payload = json.loads(path.read_text())
        instrument = payload.get("instrument", {{}})
        if (
            str(instrument.get("venue_id", "")).lower() == venue.lower()
            and str(instrument.get("native_symbol", "")).upper() == native_symbol.upper()
        ):
            snapshot = payload
            break
    assert snapshot is not None, entry
    received = datetime.fromisoformat(str(snapshot["received_timestamp"]))
    age_seconds = (datetime.now(timezone.utc) - received).total_seconds()
    assert 0 <= age_seconds <= 15, (entry, age_seconds)
    assert int(snapshot.get("sequence", 0)) > 0
    assert snapshot.get("bids") and snapshot.get("asks")
    print(
        "MARKET_" + venue.upper() + "_" + native_symbol.upper().replace("-", "")
        + "=CONNECTED sequence=" + str(snapshot["sequence"])
        + " age_seconds=" + str(round(age_seconds, 3))
    )
print("MARKET_INSTRUMENTS=" + str(len(entries)))
PY
else
  printf 'MARKET_WORKER=absent_or_disabled\\n'
fi
printf '%s\\n' '--- frontend postflight ---'
python3 - <<'PY'
import urllib.request


with urllib.request.urlopen("http://{args.bind_address}:4191/", timeout=10) as response:
    body = response.read().decode("utf-8", errors="replace")
    assert response.status == 200
    assert '<div id="app"></div>' in body
    print("FRONTEND_HTTP=" + str(response.status))
    print("FRONTEND_BODY_BYTES=" + str(len(body.encode("utf-8"))))
PY
"""
    code, output, error = run(client, command, timeout=60)
    print(output, end="")
    print(error, end="")
    return code


def diagnose(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    remote_dir = _remote_dir_expression(args.remote_dir)
    command = f"""set -u
REMOTE_DIR={remote_dir}
cd "$REMOTE_DIR"
printf '%s\\n' '--- crypto compose ---'
docker compose -p crypto-platform ps --all
printf '%s\\n' '--- container state ---'
docker inspect crypto-platform-backend-1 crypto-platform-frontend-1 \\
  --format 'NAME={{{{.Name}}}} RUNNING={{{{.State.Running}}}} STATUS={{{{.State.Status}}}} EXIT={{{{.State.ExitCode}}}} ERROR={{{{.State.Error}}}}' \\
  2>/dev/null || true
printf '%s\\n' '--- frontend logs ---'
docker compose -p crypto-platform logs --no-color --tail=100 frontend 2>/dev/null || true
printf '%s\\n' '--- backend logs ---'
docker compose -p crypto-platform logs --no-color --tail=100 backend 2>/dev/null || true
printf '%s\\n' '--- backend response probe ---'
docker compose -p crypto-platform exec -T backend python - <<'PY'
import json
import os
import urllib.error
import urllib.request


def request(path, method="GET", payload=None, token=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {{"Content-Type": "application/json"}}
    if token:
        headers["Authorization"] = f"Bearer {{token}}"
    request = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


health_status, health = request("/health")
login_status, login = request(
    "/api/v1/auth/login",
    method="POST",
    payload={{
        "username": os.environ.get("CRYPTO_ADMIN_USERNAME", "admin"),
        "password": os.environ.get("CRYPTO_ADMIN_PASSWORD", ""),
    }},
)
print("BACKEND_HEALTH_STATUS=" + str(health_status))
print("BACKEND_HEALTH_MODE=" + str(health.get("execution_mode")))
print("AUTH_LOGIN_STATUS=" + str(login_status))
if "access_token" in login:
    coverage_status, coverage = request(
        "/api/v1/history/coverage",
        token=login["access_token"],
    )
    print("COVERAGE_STATUS=" + str(coverage_status))
    print("COVERAGE_DATASETS=" + str(coverage.get("dataset_count")))
    print("COVERAGE_ROWS=" + str(coverage.get("row_count")))
    print("COVERAGE_EXPECTED=" + str(coverage.get("expected_dataset_count")))
PY
printf '%s\\n' '--- frontend response probe ---'
python3 - <<'PY'
import urllib.request


try:
    with urllib.request.urlopen("http://{args.bind_address}:4191/", timeout=10) as response:
        print("FRONTEND_HTTP=" + str(response.status))
except Exception as error:
    print("FRONTEND_ERROR=" + type(error).__name__ + ":" + str(error))
PY
"""
    code, output, error = run(client, command, timeout=90)
    print(output, end="")
    print(error, end="")
    return code


def archive(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    """Create verified Parquet mirrors in the remote history volume."""
    remote_dir = _remote_dir_expression(args.remote_dir)
    command = f"""set -eu
REMOTE_DIR={remote_dir}
cd "$REMOTE_DIR"
docker run --rm --user 0:0 --read-only -v "$PWD/data/history:/runtime/history" crypto-platform/backend:0.1.0 python scripts/prepare_history_permissions.py /runtime/history
docker compose -p crypto-platform exec -T backend python scripts/archive_history.py --data-dir /runtime/history
docker compose -p crypto-platform exec -T backend python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10) as response:
    assert response.status == 200
PY
"""
    code, output, error = run(client, command, timeout=900)
    print(output, end="")
    print(error, end="")
    return code


def add_tree(archive: tarfile.TarFile, relative_root: str) -> None:
    source = ROOT / relative_root
    if not source.exists():
        return
    if source.is_file():
        archive.add(source, arcname=relative_root, recursive=False)
        return
    for path in source.rglob("*"):
        if any(part in EXCLUDED_NAMES for part in path.parts):
            continue
        if path.is_file():
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)


def build_archive(include_history: bool) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for item in SOURCE_FILES:
            add_tree(archive, item)
        if include_history:
            history = ROOT / "data" / "history"
            for path in history.rglob("*") if history.exists() else ():
                if (
                    path.is_file()
                    and (path.suffix == ".csv" or path.name.endswith(".manifest.json"))
                    and ".runtime" not in path.parts
                ):
                    archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    return stream.getvalue()


def build_frontend_overlay_archive() -> bytes:
    """Package the already-built static frontend for a dependency-free overlay."""
    dist_root = ROOT / "frontend" / "dist"
    if not dist_root.is_dir():
        raise SystemExit(f"frontend build output is missing: {dist_root}")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        dockerfile = b"FROM crypto-platform/frontend:0.1.0\nCOPY dist /usr/share/nginx/html\n"
        info = tarfile.TarInfo("Dockerfile.frontend.overlay")
        info.size = len(dockerfile)
        archive.addfile(info, io.BytesIO(dockerfile))
        archive.add(ROOT / "compose.yaml", arcname="compose.yaml", recursive=False)
        for path in dist_root.rglob("*"):
            if path.is_file():
                archive.add(
                    path,
                    arcname=path.relative_to(ROOT / "frontend").as_posix(),
                    recursive=False,
                )
    return stream.getvalue()


def _local_history_expectations() -> tuple[int, int]:
    """Read the exact history payload expected in the deployment archive."""
    history_root = ROOT / "data" / "history"
    manifests = sorted(history_root.rglob("*.manifest.json")) if history_root.exists() else []
    total_rows = 0
    for path in manifests:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            total_rows += int(payload["row_count"])
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise SystemExit(f"invalid local history manifest: {path}: {error}") from error
    return len(manifests), total_rows


def _remote_dir_expression(remote_dir: str) -> str:
    """Return a shell-safe path expression while preserving the default HOME path."""
    if remote_dir == DEFAULT_REMOTE_DIR:
        return '"$HOME/crypto-platform"'
    if not remote_dir.startswith("/"):
        raise SystemExit("--remote-dir must be an absolute path or the default ~/crypto-platform")
    return shlex.quote(remote_dir)


def _remote_dev_env_bytes() -> bytes:
    """Adapt the local development sample to paths mounted by Compose."""
    content = DEV_ENV_SOURCE.read_text(encoding="utf-8")
    content = content.replace(
        "CRYPTO_HISTORY_DATA_PATH=data/history",
        "CRYPTO_HISTORY_DATA_PATH=/runtime/history",
    )
    return content.encode("utf-8")


def deploy(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    archive = build_archive(not args.skip_history)
    if args.bootstrap_dev_env and not DEV_ENV_SOURCE.exists():
        raise SystemExit(f"development environment template is missing: {DEV_ENV_SOURCE}")
    token = uuid.uuid4().hex
    remote_tmp = f"/tmp/crypto-platform-{token}.tar.gz"
    remote_stage = f"/tmp/crypto-platform-stage-{token}"
    remote_env_tmp = f"/tmp/crypto-platform-env-{token}" if args.bootstrap_dev_env else None
    remote_dir = _remote_dir_expression(args.remote_dir)
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_tmp, "wb") as handle:
            handle.write(archive)
        if remote_env_tmp is not None:
            with sftp.file(remote_env_tmp, "wb") as handle:
                handle.write(_remote_dev_env_bytes())
    finally:
        sftp.close()
    bootstrap_env = ""
    if remote_env_tmp is not None:
        bootstrap_env = (
            f"mkdir -p \"$REMOTE_DIR\"; "
            f"if [ ! -f \"$REMOTE_DIR/.env\" ]; then "
            f"cp {remote_env_tmp} \"$REMOTE_DIR/.env\"; chmod 600 \"$REMOTE_DIR/.env\"; "
            "printf 'BOOTSTRAPPED_DEV_ENV=true\\n'; "
            "else printf 'BOOTSTRAPPED_DEV_ENV=false\\n'; fi; "
            "if grep -q '^CRYPTO_DEV_MODE=true$' \"$REMOTE_DIR/.env\" "
            "&& grep -q '^CRYPTO_HISTORY_DATA_PATH=data/history$' \"$REMOTE_DIR/.env\"; then "
            "sed -i 's#^CRYPTO_HISTORY_DATA_PATH=data/history$#CRYPTO_HISTORY_DATA_PATH=/runtime/history#' \"$REMOTE_DIR/.env\"; "
            "printf 'REPAIRED_DEV_HISTORY_PATH=true\\n'; "
            "fi; "
        )
    bind_config = (
        f"if grep -q '^CRYPTO_BIND_ADDRESS=' \"$REMOTE_DIR/.env\"; then "
        f"sed -i 's#^CRYPTO_BIND_ADDRESS=.*$#CRYPTO_BIND_ADDRESS={args.bind_address}#' \"$REMOTE_DIR/.env\"; "
        f"else printf '\\nCRYPTO_BIND_ADDRESS={args.bind_address}\\n' >> \"$REMOTE_DIR/.env\"; fi; "
        f"printf 'CRYPTO_BIND_ADDRESS={args.bind_address}\\n'; "
    )
    command = (
        "set -eu; "
        f"REMOTE_DIR={remote_dir}; "
        + bootstrap_env
        + "if [ ! -f \"$REMOTE_DIR/.env\" ]; then "
        "echo 'remote .env is required; provide credentials out of band' >&2; exit 2; fi; "
        "for key in CRYPTO_ADMIN_PASSWORD CRYPTO_SESSION_SECRET; do "
        "if ! awk -F= -v wanted=\"$key\" '$1 == wanted && length($0) > length(wanted) + 1 { found=1; exit } END { exit(found ? 0 : 1) }' \"$REMOTE_DIR/.env\"; then "
        "echo \"remote .env is missing $key\" >&2; exit 2; fi; done; "
        + bind_config
        + "mkdir -p \"$REMOTE_DIR\"; "
        f"REMOTE_STAGE={remote_stage}; rm -rf \"$REMOTE_STAGE\"; mkdir -p \"$REMOTE_STAGE\"; "
        f"tar -xzf {remote_tmp} -C {remote_stage}; "
        f"rm -f {remote_tmp}; "
        "for entry in .dockerignore .gitignore Dockerfile compose.yaml pyproject.toml README.md backend contracts docs frontend scripts src; do "
        "if [ -e \"$REMOTE_STAGE/$entry\" ]; then cp -a \"$REMOTE_STAGE/$entry\" \"$REMOTE_DIR/\"; fi; done; "
        "cd \"$REMOTE_DIR\"; "
        "docker compose -p crypto-platform config >/tmp/crypto-platform-config.txt; "
        "docker compose -p crypto-platform build backend frontend; "
        "mkdir -p data/history; "
        "if [ -d \"$REMOTE_STAGE/data/history\" ]; then "
        f"docker run --rm --user 0:0 -v \"$REMOTE_STAGE/data/history:/incoming:ro\" -v \"$REMOTE_DIR/data/history:/target\" crypto-platform/backend:0.1.0 "
        "sh -c 'set -eu; find /incoming -type f \\( -name \"*.csv\" -o -name \"*.manifest.json\" \\) -print | "
        "while IFS= read -r source_path; do "
        "relative_path=${source_path#/incoming/}; target_path=\"/target/$relative_path\"; "
        "mkdir -p \"$(dirname \"$target_path\")\"; temporary_path=\"$target_path.deploy\"; "
        "cp -- \"$source_path\" \"$temporary_path\"; mv -f -- \"$temporary_path\" \"$target_path\"; "
        "done'; fi; "
        "printf 'SYNCED_DIR=%s\\n' \"$PWD\"; "
        "printf 'HISTORY_CSV='; find data/history -type f -name '*.csv' 2>/dev/null | wc -l; "
        "printf 'HISTORY_MANIFEST='; find data/history -type f -name '*.manifest.json' 2>/dev/null | wc -l; "
        "mkdir -p data/history/.runtime runtime/market; "
        "docker run --rm --user 0:0 --read-only "
        "-v \"$PWD/data/history/.runtime:/runtime/state\" "
        "crypto-platform/backend:0.1.0 chown 65532:65532 /runtime/state; "
        "docker run --rm --user 0:0 --read-only "
        "-v \"$PWD/data/history:/runtime/history\" "
        "crypto-platform/backend:0.1.0 python scripts/prepare_history_permissions.py /runtime/history; "
        "docker run --rm --user 0:0 --read-only "
        "-v \"$PWD/runtime/market:/runtime/market\" "
        "crypto-platform/backend:0.1.0 chown 65532:65532 /runtime/market; "
        "docker compose -p crypto-platform up -d backend frontend task-worker task-scheduler; "
        "market_worker_id=$(docker compose -p crypto-platform ps -q market-worker 2>/dev/null || true); "
        "if [ -n \"$market_worker_id\" ] && [ \"$(docker inspect -f '{{.State.Running}}' \"$market_worker_id\")\" = true ]; then "
        "docker compose -p crypto-platform --profile market up -d --force-recreate market-worker; "
        "printf 'MARKET_WORKER_RECREATED=true\\n'; "
        "else printf 'MARKET_WORKER_RECREATED=false\\n'; fi; "
        "docker compose -p crypto-platform ps --all"
    )
    try:
        code, output, error = run(client, command, timeout=1800)
        print(output, end="")
        print(error, end="")
        return code
    finally:
        cleanup_paths = remote_tmp if remote_env_tmp is None else f"{remote_tmp} {remote_env_tmp}"
        cleanup = f"rm -f {cleanup_paths}; rm -rf {remote_stage}"
        run(client, cleanup, timeout=15)


def hotfix(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    """Update backend/worker code by layering it on the existing runtime image."""
    archive = build_archive(False)
    token = uuid.uuid4().hex
    remote_tmp = f"/tmp/crypto-platform-overlay-{token}.tar.gz"
    remote_stage = f"/tmp/crypto-platform-overlay-stage-{token}"
    remote_dir = _remote_dir_expression(args.remote_dir)
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_tmp, "wb") as handle:
            handle.write(archive)
    finally:
        sftp.close()
    command = (
        "set -eu; "
        f"REMOTE_DIR={remote_dir}; "
        f"REMOTE_TMP={remote_tmp}; REMOTE_STAGE={remote_stage}; "
        "rm -rf \"$REMOTE_STAGE\"; mkdir -p \"$REMOTE_STAGE\"; "
        "tar -xzf \"$REMOTE_TMP\" -C \"$REMOTE_STAGE\"; rm -f \"$REMOTE_TMP\"; "
        "cd \"$REMOTE_DIR\"; "
        "cp \"$REMOTE_STAGE/compose.yaml\" \"$REMOTE_DIR/compose.yaml\"; "
        f"if grep -q '^CRYPTO_BIND_ADDRESS=' \"$REMOTE_DIR/.env\"; then sed -i 's#^CRYPTO_BIND_ADDRESS=.*$#CRYPTO_BIND_ADDRESS={args.bind_address}#' \"$REMOTE_DIR/.env\"; else printf '\\nCRYPTO_BIND_ADDRESS={args.bind_address}\\n' >> \"$REMOTE_DIR/.env\"; fi; "
        "test -f \"$REMOTE_STAGE/Dockerfile.overlay\"; "
        "docker build -t crypto-platform/backend:0.1.0-hotfix -f \"$REMOTE_STAGE/Dockerfile.overlay\" \"$REMOTE_STAGE\"; "
        "CRYPTO_BACKEND_IMAGE=crypto-platform/backend:0.1.0-hotfix "
        "docker compose -p crypto-platform --profile market up -d --force-recreate backend market-worker task-worker task-scheduler; "
        "docker compose -p crypto-platform ps --all"
    )
    try:
        code, output, error = run(client, command, timeout=600)
        print(output, end="")
        print(error, end="")
        return code
    finally:
        run(client, f"rm -rf {remote_stage} {remote_tmp}", timeout=15)


def frontend_hotfix(client: paramiko.SSHClient, args: argparse.Namespace) -> int:
    """Overlay local frontend dist on the existing nginx runtime image."""
    archive = build_frontend_overlay_archive()
    token = uuid.uuid4().hex
    remote_tmp = f"/tmp/crypto-platform-frontend-overlay-{token}.tar.gz"
    remote_stage = f"/tmp/crypto-platform-frontend-overlay-stage-{token}"
    remote_dir = _remote_dir_expression(args.remote_dir)
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_tmp, "wb") as handle:
            handle.write(archive)
    finally:
        sftp.close()
    command = (
        "set -eu; "
        f"REMOTE_DIR={remote_dir}; "
        f"REMOTE_TMP={remote_tmp}; REMOTE_STAGE={remote_stage}; "
        "rm -rf \"$REMOTE_STAGE\"; mkdir -p \"$REMOTE_STAGE\"; "
        "tar -xzf \"$REMOTE_TMP\" -C \"$REMOTE_STAGE\"; rm -f \"$REMOTE_TMP\"; "
        "cd \"$REMOTE_DIR\"; "
        "cp \"$REMOTE_STAGE/compose.yaml\" \"$REMOTE_DIR/compose.yaml\"; "
        "test -f \"$REMOTE_STAGE/Dockerfile.frontend.overlay\"; "
        "docker build -t crypto-platform/frontend:0.1.0-hotfix "
        "-f \"$REMOTE_STAGE/Dockerfile.frontend.overlay\" \"$REMOTE_STAGE\"; "
        "CRYPTO_BACKEND_IMAGE=crypto-platform/backend:0.1.0-hotfix "
        "CRYPTO_FRONTEND_IMAGE=crypto-platform/frontend:0.1.0-hotfix "
        "docker compose -p crypto-platform up -d --force-recreate frontend; "
        "docker inspect -f 'IMAGE={{.Config.Image}} RESTARTS={{.RestartCount}}' crypto-platform-frontend-1; "
        "docker compose -p crypto-platform ps --all"
    )
    try:
        code, output, error = run(client, command, timeout=300)
        print(output, end="")
        print(error, end="")
        return code
    finally:
        run(client, f"rm -rf {remote_stage} {remote_tmp}", timeout=15)


def main() -> int:
    args = parse_args()
    client = connect(args)
    try:
        if args.action == "inventory":
            return inventory(client, args)
        if args.action == "postflight":
            return postflight(client, args)
        if args.action == "diagnose":
            return diagnose(client, args)
        if args.action == "archive":
            return archive(client, args)
        if args.action == "hotfix":
            return hotfix(client, args)
        if args.action == "frontend-hotfix":
            return frontend_hotfix(client, args)
        return deploy(client, args)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
