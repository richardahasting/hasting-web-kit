#!/usr/bin/env bash
# Publish the kit assets to the versioned serving directory.
#
#   ./deploy/release-kit.sh v1
#
# Copies kit/* to /var/www/feedback-kit/<version>/ — a frozen snapshot, so a
# later repo push can never silently change what live sites load. Publishing
# over an EXISTING version is refused unless FORCE=1 (patch releases within a
# version line are allowed but deliberate).
set -euo pipefail

VERSION="${1:?usage: release-kit.sh <version, e.g. v1>}"
DEST="/var/www/feedback-kit/${VERSION}"

if [ -d "$DEST" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "Refusing to overwrite existing ${DEST} (set FORCE=1 for a deliberate patch release)" >&2
    exit 1
fi

sudo mkdir -p "$DEST"
sudo cp kit/tokens.css kit/base.css kit/feedback.js "$DEST/"
sudo chmod 644 "$DEST"/*
echo "Published kit ${VERSION}:"
ls -l "$DEST"
