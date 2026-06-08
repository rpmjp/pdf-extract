FROM postgres:16-bookworm

# Install pgBackRest (WAL archiving + scheduled full backups)
# Install supercronic (zero-dependency cron for containers)
ARG SUPERCRONIC_VERSION=0.2.33
RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest curl \
    && curl -fsSL \
       "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
       -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic \
    && rm -rf /var/lib/apt/lists/*

# pgBackRest config — repository lives on the pgbackups volume (repo1-path)
COPY pgbackrest.conf /etc/pgbackrest/pgbackrest.conf

# Stanza init hook: runs once during first postgres initdb
COPY initdb-pgbackrest.sh /docker-entrypoint-initdb.d/01-pgbackrest-stanza.sh
RUN chmod +x /docker-entrypoint-initdb.d/01-pgbackrest-stanza.sh

# Entrypoint for the pgbackrest-cron sidecar (NOT the postgres daemon)
COPY pgbackrest-cron-entrypoint.sh /usr/local/bin/pgbackrest-cron-entrypoint.sh
RUN chmod +x /usr/local/bin/pgbackrest-cron-entrypoint.sh

# Backup schedule for supercronic
COPY backup-schedule /etc/backup-schedule
