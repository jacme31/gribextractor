#!/bin/sh
set -e

# Create group if needed
if ! getent group "$PGID" >/dev/null 2>&1; then
    groupmod -g "$PGID" appgroup 2>/dev/null || groupadd -g "$PGID" appgroup
fi

# Create user with PUID
if ! id -u "$PUID" >/dev/null 2>&1; then
    usermod -u "$PUID" appuser 2>/dev/null || useradd -u "$PUID" -g "$PGID" -m appuser
else
    # If user with same UID exists, just adjust group
    usermod -g "$PGID" appuser
fi

# Fix ownership of the app directory
chown -R "$PUID:$PGID" /app

# Execute the command as the requested user
exec gosu "$PUID:$PGID" "$@"
