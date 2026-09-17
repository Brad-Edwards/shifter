# Guacamole Client
#
# Uses the official guacamole/guacamole image with a custom entrypoint that
# handles idempotent database schema initialization.
#
# The base image handles:
# - Extension loading (via JSON_ENABLED=true, OPENID_ENABLED=false)
#
# Our entrypoint adds:
# - Automatic schema creation on first startup (idempotent)
#
# Required environment variables:
# - JSON_SECRET_KEY: 128-bit hex key for signing/encrypting JSON auth payloads
# - POSTGRESQL_HOSTNAME, POSTGRESQL_DATABASE, POSTGRESQL_USER, POSTGRESQL_PASSWORD
#
# Reference: https://guacamole.apache.org/doc/gug/guacamole-docker.html

# Base image pinned by digest for reproducibility.
#
# This digest is the guacamole/guacamole:latest rebuild published 2026-09-17:
# the Guacamole 1.6.0 web application rebuilt on Apache Tomcat 9.0.122 and
# Ubuntu 24.04. It is pinned by digest (not by the mutable :latest tag) so
# builds stay reproducible.
#
# Why this digest and not the versioned :1.6.0 tag: the released :1.6.0 tag
# bundles Tomcat 9.0.106, which still carries fixable HIGH/CRITICAL Tomcat
# CVEs (fixes require >= 9.0.107) plus an older Ubuntu with fixable OS CVEs.
# Pinning the 9.0.122 rebuild clears all Tomcat + OS HIGH/CRITICAL findings.
# Re-point this digest when a versioned Guacamole release ships an
# equally-patched (or newer) Tomcat/OS base.
ARG GUACAMOLE_VERSION=1.6.0
# base image: guacamole 1.6.0 webapp on Tomcat 9.0.122 / Ubuntu 24.04 (keep in sync with the digest below)
FROM guacamole/guacamole@sha256:10417af3f1920ce443b6ccc46ae275b7033847cc0031dcc30fbf0684d280e69c

# Install PostgreSQL client for schema initialization
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Add custom entrypoint for idempotent schema initialization
COPY entrypoint.sh /opt/guacamole/bin/custom-entrypoint.sh
RUN chmod +x /opt/guacamole/bin/custom-entrypoint.sh

USER guacamole

ENTRYPOINT ["/opt/guacamole/bin/custom-entrypoint.sh"]
CMD ["/opt/guacamole/bin/start.sh"]
