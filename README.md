# Docker Containers Monitor

Monitors all Docker containers on the host VM and stores container state snapshots in a PostgreSQL database.

## Overview

This script collects metadata for running and stopped Docker containers and periodically saves the results into a database. It is intended for infrastructure observability and forensic tracking of container lifecycle events.

Captured fields per container:
- `snapshot_time`
- `hostname`
- `container_id`
- `container_name`
- `image_name`
- `container_created_at`
- `running_for`
- `status`

## Requirements

- Python 3.9+
- Docker CLI installed and accessible to the user
- PostgreSQL 12+
- Python dependencies:
  ```bash
  pip install psycopg2-binary python-dateutil pyyaml

