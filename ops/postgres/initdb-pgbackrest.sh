#!/bin/bash
# Runs ONCE inside the postgres container during first database initialization.
# At this point postgres is accepting connections on the local socket.
set -e

echo "==> pgBackRest: creating stanza 'main'..."
pgbackrest --stanza=main stanza-create --log-level-console=info

echo "==> pgBackRest: stanza created. WAL archiving is now active."
