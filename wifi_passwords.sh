#!/usr/bin/env bash
# Retrieves saved WiFi passwords stored on this machine.
# Linux: reads NetworkManager profiles (requires root or group membership).
# macOS: queries the system Keychain.
# Windows (Git Bash / WSL): uses netsh.

set -euo pipefail

show_linux() {
    local dir="/etc/NetworkManager/system-connections"
    if [[ ! -d "$dir" ]]; then
        echo "NetworkManager profiles not found at $dir" >&2
        exit 1
    fi

    printf "%-35s %s\n" "SSID" "Password"
    printf "%-35s %s\n" "----" "--------"

    for profile in "$dir"/*; do
        [[ -f "$profile" ]] || continue
        local ssid password
        ssid=$(grep -i "^ssid=" "$profile" 2>/dev/null | cut -d= -f2- || true)
        password=$(grep -i "^psk=" "$profile" 2>/dev/null | cut -d= -f2- || true)
        [[ -n "$ssid" ]] || continue
        printf "%-35s %s\n" "$ssid" "${password:-(open/no password)}"
    done
}

show_macos() {
    # List all AirPort network names from the Keychain
    local ssids
    ssids=$(security find-generic-password -D "AirPort network password" -a "" 2>&1 \
        | grep "acct" | sed 's/.*"acct"<blob>="//;s/"//' || true)

    if [[ -z "$ssids" ]]; then
        # Alternative: use airport utility
        ssids=$(networksetup -listpreferredwirelessnetworks en0 2>/dev/null \
            | tail -n +2 | sed 's/^\t//' || true)
    fi

    printf "%-35s %s\n" "SSID" "Password"
    printf "%-35s %s\n" "----" "--------"

    while IFS= read -r ssid; do
        [[ -n "$ssid" ]] || continue
        local password
        password=$(security find-generic-password -D "AirPort network password" \
            -a "$ssid" -w 2>/dev/null || echo "(unable to retrieve)")
        printf "%-35s %s\n" "$ssid" "$password"
    done <<< "$ssids"
}

show_windows() {
    # Works in Git Bash or WSL with netsh available
    local profiles
    profiles=$(netsh.exe wlan show profiles 2>/dev/null \
        | grep "All User Profile" | sed 's/.*: //' || true)

    printf "%-35s %s\n" "SSID" "Password"
    printf "%-35s %s\n" "----" "--------"

    while IFS= read -r ssid; do
        [[ -n "$ssid" ]] || continue
        local password
        password=$(netsh.exe wlan show profile name="$ssid" key=clear 2>/dev/null \
            | grep "Key Content" | sed 's/.*: //' || echo "(open/no password)")
        printf "%-35s %s\n" "$ssid" "$password"
    done <<< "$profiles"
}

main() {
    case "$(uname -s)" in
        Linux)
            if [[ $EUID -ne 0 ]]; then
                echo "Root access required on Linux. Re-running with sudo..."
                exec sudo "$0" "$@"
            fi
            show_linux
            ;;
        Darwin)
            show_macos
            ;;
        MINGW*|MSYS*|CYGWIN*)
            show_windows
            ;;
        *)
            # WSL: try netsh first, fall back to Linux NetworkManager
            if command -v netsh.exe &>/dev/null; then
                show_windows
            else
                if [[ $EUID -ne 0 ]]; then
                    exec sudo "$0" "$@"
                fi
                show_linux
            fi
            ;;
    esac
}

main "$@"
