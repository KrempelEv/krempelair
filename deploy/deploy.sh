#!/bin/bash
# Vom Entwicklungsrechner aus auf den Pi bringen und installieren.
#
#   deploy/deploy.sh [pi@192.168.1.100]
set -euo pipefail

HOST="${1:-pi@192.168.1.100}"
cd "$(dirname "$0")/.."

COPYFILE_DISABLE=1 tar czf - --no-xattrs --no-mac-metadata --exclude=__pycache__ krempelair deploy \
    | ssh "$HOST" 'rm -rf /tmp/krempelair-deploy && mkdir /tmp/krempelair-deploy \
        && tar xzf - -C /tmp/krempelair-deploy \
        && sudo bash /tmp/krempelair-deploy/deploy/install.sh; rc=$?; rm -rf /tmp/krempelair-deploy; exit $rc'
