#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
# Base URL of the ZTP Flask proxy (no trailing slash). Switch reaches this; Flask reaches NetBox.
ZTP_FLASK_BASE_URL="http://10.130.18.62:8080"
# Optional shared secret; must match ZTP_PROXY_API_KEY on the Flask container if set.
ZTP_PROXY_API_KEY=""
# Target Cumulus Linux release; devices not running this version will be re-imaged.
CUMULUS_TARGET_RELEASE="5.16.1"
IMAGE_URL="${ZTP_FLASK_BASE_URL}/images/cumulus-linux-${CUMULUS_TARGET_RELEASE}-mlx-amd64.bin"
ZTP_SCRIPT_URL="${ZTP_FLASK_BASE_URL}/ztp/script"

TEMP_CONFIG="/tmp/ztp_config.json"

exec >> /var/log/autoprovision 2>&1
date "+%FT%T ztp starting script $0"

log_info() { echo "$(date '+%FT%T') INFO: $1"; }
log_error() { echo "$(date '+%FT%T') ERROR: $1" >&2; }

SERIAL=$(nv show platform | grep serial | awk '{print $2}')
log_info "Device serial: $SERIAL"

# --- System image check ---
CUMULUS_CURRENT_RELEASE=$(grep DISTRIB_RELEASE /etc/lsb-release | cut -d "=" -f2)
log_info "Current release: $CUMULUS_CURRENT_RELEASE, target release: $CUMULUS_TARGET_RELEASE"

if [ "$CUMULUS_TARGET_RELEASE" != "$CUMULUS_CURRENT_RELEASE" ]; then
    log_info "Release mismatch — upgrading image from $CUMULUS_CURRENT_RELEASE to $CUMULUS_TARGET_RELEASE"
    log_info "Image URL: $IMAGE_URL"
    if ip vrf exec mgmt /usr/cumulus/bin/onie-install -fa -i "$IMAGE_URL" -z "$ZTP_SCRIPT_URL" 2>&1; then
        log_info "onie-install scheduled successfully, rebooting"
    else
        log_error "onie-install failed"
        exit 1
    fi
    nv action reboot system
    exit 0
fi
log_info "Release matches target, proceeding with provisioning"

CURL_COMMON=(-sS -f --connect-timeout 10 --max-time 30)
CURL_HEADERS=(-H "Accept: application/json")
if [ -n "${ZTP_PROXY_API_KEY:-}" ]; then
  CURL_HEADERS+=(-H "X-ZTP-Proxy-Key: ${ZTP_PROXY_API_KEY}")
fi

log_info "Fetching rendered configuration from ZTP proxy"
rm -f "$TEMP_CONFIG"

if ip vrf exec mgmt curl "${CURL_COMMON[@]}" "${CURL_HEADERS[@]}" \
    -o "$TEMP_CONFIG" \
    "${ZTP_FLASK_BASE_URL}/ztp/nvue-config/${SERIAL}" 2>&1; then
    log_info "Successfully fetched configuration JSON from ZTP proxy"
else
    log_error "Failed to fetch configuration from ZTP proxy"
    exit 1
fi

if [ ! -s "$TEMP_CONFIG" ]; then
    log_error "Empty configuration response from ZTP proxy"
    exit 1
fi

log_info "Configuration file size: $(wc -c < "$TEMP_CONFIG") bytes"


log_info "Applying NVUE configuration"
if nv config replace "$TEMP_CONFIG" 2>&1; then
    log_info "Configuration replaced successfully"
else
    log_error "Failed to replace configuration"
    exit 1
fi

nv set system aaa user cumulus hashed-password '$6$Y8fo7kDqMkFzIa7z$NsaQiO/f3NqrCBehSWE2ZUsKbPHhFxHvdDZUcZ34XhTs/TAJ4IjoCvdPRv8qQ9H2SLwDtPFETMxr9hDfJJ867.'


log_info "Applying all NVUE configuration changes"
if nv config apply -y --assume-yes 2>&1; then
    log_info "NVUE configuration applied successfully"
else
    log_error "Failed to apply NVUE configuration"
    exit 1
fi

# Restart FRR and switchd services after applying the configuration
sleep 120

          
nv config save 2>&1
rm -f "$TEMP_CONFIG"
log_info "ZTP provisioning completed successfully"
log_info "Hostname: $(hostname)"
log_info "Notifying ZTP proxy of completion"
ip vrf exec mgmt curl "${CURL_COMMON[@]}" \
    -X POST -H "Content-Type: application/json" \
    "${CURL_HEADERS[@]}" \
    -d "{\"serial\":\"${SERIAL}\",\"hostname\":\"$(hostname)\"}" \
    "${ZTP_FLASK_BASE_URL}/ztp/complete" 2>&1 || true
nv action reboot system
exit 0

# CUMULUS-AUTOPROVISIONING
