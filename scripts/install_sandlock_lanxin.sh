#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-/mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp}"
REPO="${REPO:-$BASE/sandlock}"
BIN_DIR="${BIN_DIR:-$BASE/bin}"
SANDLOCK_REPO="${SANDLOCK_REPO:-https://github.com/multikernel/sandlock.git}"

export CARGO_HOME="${CARGO_HOME:-$BASE/cargo-home}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$BASE/cargo-target}"
export PATH="$HOME/.cargo/bin:$CARGO_HOME/bin:$PATH"

mkdir -p "$BASE" "$BIN_DIR" "$CARGO_HOME" "$CARGO_TARGET_DIR"

if ! command -v cargo >/dev/null 2>&1; then
  cat >&2 <<EOF
cargo is not in PATH.
On this Lanxin image rustup may exist under ~/.cargo/bin; otherwise install Rust
to NVMe first, for example:
  curl https://sh.rustup.rs -sSf | CARGO_HOME=$CARGO_HOME sh -s -- -y --profile minimal
EOF
  exit 1
fi

if [ ! -d "$REPO/.git" ]; then
  git clone --depth 1 "$SANDLOCK_REPO" "$REPO"
else
  git -C "$REPO" fetch --depth 1 origin main
  git -C "$REPO" reset --hard origin/main
fi

cargo build --release --manifest-path "$REPO/Cargo.toml" -p sandlock-cli
install -m 0755 "$CARGO_TARGET_DIR/release/sandlock" "$BIN_DIR/sandlock"

cat <<EOF
sandlock installed:
  $BIN_DIR/sandlock

Run the Agent API with:
  export ZETTAI_SANDLOCK_BIN=$BIN_DIR/sandlock
  cd /mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/Zettai.github.io
  scripts/run_agent_api_lanxin.sh
EOF
