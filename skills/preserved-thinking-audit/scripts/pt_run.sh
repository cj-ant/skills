#!/usr/bin/env bash
# Start the proxy, run one command (or an interactive shell) against it, stop the proxy, analyze the log.
# Proxy and harness share one process tree, which matters in agent sandboxes that give each shell command its own
# network namespace: a proxy started in one command is unreachable from the next.
#
#   pt_run.sh <harness> <mode> [--run-id ID] [--port N] [proxy flags...] -- <command...>
#   pt_run.sh my-harness drop_block --run-id r1 -- ./drive.sh
#
# The command sees PT_BASE_URL (no /v1), PT_BASE_URL_V1, PT_MARK (a helper script path), PT_OUT, and PT_HARNESS_DIR.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
harness="${1:?harness}"; mode="${2:?mode}"; shift 2
port=8484; run_id="$(date +%Y%m%d-%H%M%S)"; proxy_args=()
while [[ $# -gt 0 && "$1" != "--" ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --run-id) run_id="$2"; shift 2 ;;
    *) proxy_args+=("$1"); shift ;;
  esac
done
[[ "${1:-}" == "--" ]] && shift
out="${PT_AUDIT_ROOT:-$HOME/pt-audit}/$harness/$run_id-$mode"
mkdir -p "$out"; chmod 700 "$out"
# The real key goes to the proxy on a file descriptor and is removed from this shell's environment, so the driver
# and the harness it starts never inherit it. A process running as the same user can still reach the proxy's memory
# or this shell's original /proc/<pid>/environ: that is why the harness belongs in a container, VM, or other account.
key="${PT_UPSTREAM_API_KEY:-${ANTHROPIC_API_KEY:-}}"
unset PT_UPSTREAM_API_KEY ANTHROPIC_API_KEY
token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
key_args=()
[[ -n "$key" ]] && key_args=(--key-fd 3)
# ${arr[@]+"${arr[@]}"} keeps bash 3.2 (macOS) from treating an empty array as unset under set -u.
PT_CONTROL_TOKEN="$token" python3 "$here/pt_proxy.py" --harness "$harness" --mode "$mode" --port "$port" --run-id "$run_id" --out "$out" \
  ${key_args[@]+"${key_args[@]}"} ${proxy_args[@]+"${proxy_args[@]}"} 2> "$out/proxy.log" 3< <(printf %s "$key") &
proxy_pid=$!
unset key
trap 'kill $proxy_pid 2>/dev/null || true; wait $proxy_pid 2>/dev/null || true' EXIT
# Wait for this proxy, not some other listener on the port: the status reply must carry our run id.
for _ in $(seq 1 100); do
  if curl -s --noproxy '*' "http://127.0.0.1:$port/__pt/status" 2>/dev/null | grep -q "\"run_id\": \"$run_id\""; then break; fi
  if ! kill -0 $proxy_pid 2>/dev/null; then echo "proxy exited:" >&2; cat "$out/proxy.log" >&2; exit 1; fi
  sleep 0.1
done
if ! kill -0 $proxy_pid 2>/dev/null || ! curl -s --noproxy '*' "http://127.0.0.1:$port/__pt/status" 2>/dev/null | grep -q "\"run_id\": \"$run_id\""; then
  echo "proxy did not come up on port $port (is another pt_proxy or service using it? try --port)" >&2; cat "$out/proxy.log" >&2; exit 1
fi
export PT_BASE_URL="http://127.0.0.1:$port" PT_BASE_URL_V1="http://127.0.0.1:$port/v1" PT_OUT="$out" PT_PORT="$port"
export PT_HARNESS_DIR="${PT_HARNESS_DIR:-${PT_AUDIT_ROOT:-$HOME/pt-audit}/$harness}"   # the driver's folder: workspace/, xdg/, drive.sh
cat > "$out/mark" <<MARK
#!/usr/bin/env bash
curl -s --noproxy '*' -G "http://127.0.0.1:$port/__pt/mark" --data-urlencode "token=$token" --data-urlencode "label=\$1" --data-urlencode "note=\${2:-}" | grep -q '"ok": true' && echo "[pt] mark \$1" || echo "[pt] mark \$1 FAILED (proxy gone or token rejected)" >&2
MARK
chmod 700 "$out/mark"; export PT_MARK="$out/mark"
# A dummy key for anything that insists on one. Never the real key.
export ANTHROPIC_API_KEY=sk-ant-dummy
status=0
if [[ $# -gt 0 ]]; then "$@" || status=$?; else "${SHELL:-bash}" || status=$?; fi
kill $proxy_pid 2>/dev/null || true; wait $proxy_pid 2>/dev/null || true; trap - EXIT
python3 "$here/pt_analyze.py" "$out" || true
exit $status
