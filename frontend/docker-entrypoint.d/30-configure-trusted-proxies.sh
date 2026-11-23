#!/bin/sh
set -eu

config_file="/etc/nginx/trusted-proxies.conf"
: > "$config_file"

trusted_proxies=${ECC_TRUSTED_PROXY_CIDRS:-}
[ -n "$trusted_proxies" ] || exit 0

old_ifs=$IFS
IFS=','
for cidr in $trusted_proxies; do
    case "$cidr" in
        ''|*[!0-9A-Fa-f:./]*)
            echo "Invalid ECC_TRUSTED_PROXY_CIDRS entry: $cidr" >&2
            exit 1
            ;;
    esac

    case "$cidr" in
        */*)
            prefix=${cidr##*/}
            case "$prefix" in
                ''|*[!0-9]*)
                    echo "Invalid proxy network prefix: $cidr" >&2
                    exit 1
                    ;;
            esac
            if [ "$prefix" -gt 128 ]; then
                echo "Proxy network prefix is out of range: $cidr" >&2
                exit 1
            fi
            ;;
    esac

    printf 'set_real_ip_from %s;\n' "$cidr" >> "$config_file"
done
IFS=$old_ifs
