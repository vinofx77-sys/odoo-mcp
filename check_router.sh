#!/usr/bin/env bash
# Checks whether the default network gateway (router) is reachable.
# Detects the gateway automatically; optionally accepts a custom IP as $1.

set -euo pipefail

get_gateway() {
    local gw=""
    # Linux
    if command -v ip &>/dev/null; then
        gw=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
    fi
    # macOS / BSD
    if [[ -z "$gw" ]] && command -v route &>/dev/null; then
        gw=$(route -n get default 2>/dev/null | awk '/gateway/ {print $2; exit}')
    fi
    # Windows (netstat via Git Bash / WSL)
    if [[ -z "$gw" ]] && command -v netstat &>/dev/null; then
        gw=$(netstat -rn 2>/dev/null | awk '/^0\.0\.0\.0/ {print $2; exit}')
    fi
    echo "$gw"
}

ping_host() {
    local host="$1" count=4
    if [[ "$(uname -s)" == "Darwin" ]]; then
        ping -c "$count" -W 1000 "$host"
    else
        ping -c "$count" -W 1 "$host"
    fi
}

check_http() {
    local host="$1"
    if command -v curl &>/dev/null; then
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "http://$host" 2>/dev/null || echo "000")
        echo "HTTP admin page: http://$host  →  status $code"
    fi
}

main() {
    local target="${1:-}"

    if [[ -z "$target" ]]; then
        target=$(get_gateway)
        if [[ -z "$target" ]]; then
            echo "Could not detect default gateway. Pass the router IP as an argument." >&2
            exit 1
        fi
        echo "Detected gateway: $target"
    else
        echo "Target: $target"
    fi

    echo ""
    echo "Pinging $target ..."
    if ping_host "$target"; then
        echo ""
        echo "Router is UP."
    else
        echo ""
        echo "Router is DOWN or not responding to ICMP."
        exit 1
    fi

    echo ""
    check_http "$target"
}

main "$@"
