#!/usr/bin/env python3
"""
Docker Containers Monitor

Collects information about running and stopped Docker containers on the host VM
and stores snapshots into the PostgreSQL database configured in config/config.yaml.

Captured fields per container: snapshot_time, hostname, container_id, name,
image, container_created_at, status.
"""

import sys
import os
import json
import yaml
import time
import socket
import logging
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Any

import psycopg2
from dateutil import parser as dateutil_parser


class DockerMonitor:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self._setup_logging()

    def _load_config(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logging.error(f"Config file not found: {path}")
            sys.exit(1)
        except yaml.YAMLError as e:
            logging.error(f"Error parsing config file: {e}")
            sys.exit(1)

    def _setup_logging(self) -> None:
        log_format = '%(asctime)s UTC %(levelname)s %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'

        monitor_cfg = self.config.get('docker_monitor', {})
        log_file = monitor_cfg.get('log_file', 'logs/docker_monitor.log')

        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        root_logger = logging.getLogger()
        root_logger.handlers.clear()

        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            datefmt=date_format,
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_file)
            ],
            force=True
        )

        log_level = self.config.get('docker_monitor', {}).get('level', 'INFO')
        root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    def _db_connect(self):
        db_cfg = self.config['database']
        return psycopg2.connect(
            host=db_cfg['host'],
            port=db_cfg['port'],
            dbname=db_cfg['database'],
            user=db_cfg['username'],
            password=db_cfg['password']
        )

    def _run_docker_ps(self) -> List[Dict[str, Any]]:
        """Run `docker ps -a` and parse each line of JSON format."""
        try:
            result = subprocess.run(
                [
                    'docker', 'ps', '-a',
                    '--no-trunc',
                    '--format', '{{json .}}'
                ],
                capture_output=True,
                text=True,
                check=True
            )
        except FileNotFoundError:
            logging.error("docker command not found on this system.")
            return []
        except subprocess.CalledProcessError as e:
            logging.error(f"docker ps failed: {e.stderr.strip() if e.stderr else e}")
            return []

        containers: List[Dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                containers.append(obj)
            except json.JSONDecodeError:
                # Ignore unparsable lines
                continue
        return containers

    def _parse_created_at(self, created_at_str: str) -> datetime:
        if not created_at_str:
            return None  # type: ignore
        try:
            dt = dateutil_parser.parse(created_at_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None  # type: ignore

    def collect_snapshot(self) -> List[Dict[str, Any]]:
        hostname = socket.gethostname()
        snapshot_time = datetime.now(timezone.utc)
        raw = self._run_docker_ps()

        snapshot: List[Dict[str, Any]] = []
        for c in raw:
            # Fields as exposed by docker template: ID, Image, Names, CreatedAt, Status
            container = {
                'snapshot_time': snapshot_time,
                'hostname': hostname,
                'container_id': c.get('ID') or '',
                'container_name': c.get('Names') or '',
                'image_name': c.get('Image') or '',
                'container_created_at': self._parse_created_at(c.get('CreatedAt') or ''),
                'running_for': c.get('RunningFor') or '',
                'status': c.get('State') or '',
            }
            snapshot.append(container)
        return snapshot

    def store_snapshot(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            logging.info("No containers found; nothing to store.")
            return

        try:
            conn = self._db_connect()
            cur = conn.cursor()

            # Use UPSERT (INSERT ... ON CONFLICT ... DO UPDATE SET)
            upsert_sql = """
                INSERT INTO container_status (
                    snapshot_time, hostname, container_id, container_name,
                    image_name, container_created_at, running_for, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (hostname, container_id)
                DO UPDATE SET
                    snapshot_time = EXCLUDED.snapshot_time,
                    container_name = EXCLUDED.container_name,
                    image_name = EXCLUDED.image_name,
                    container_created_at = EXCLUDED.container_created_at,
                    running_for = EXCLUDED.running_for,
                    status = EXCLUDED.status,
                    updated_at = NOW()
            """

            params = [
                (
                    r['snapshot_time'],
                    r['hostname'],
                    r['container_id'],
                    r['container_name'],
                    r['image_name'],
                    r['container_created_at'],
                    r['running_for'],
                    r['status'],
                )
                for r in rows
            ]

            cur.executemany(upsert_sql, params)
            conn.commit()

            # Get count of affected rows
            rows_affected = cur.rowcount
            cur.close()
            conn.close()

            logging.info(f"Updated/inserted {len(rows)} container records ({rows_affected} rows affected) in PostgreSQL.")

            monitor_cfg = self.config.get('docker_monitor', {})
            period_seconds = int(monitor_cfg.get('period_seconds', 300))
            logging.info(f"Waiting for {period_seconds} seconds before next run.")

        except Exception as e:
            logging.error(f"Failed to store snapshot to PostgreSQL: {e}")

    def run_single_cycle(self) -> None:
        try:
            rows = self.collect_snapshot()
            self.store_snapshot(rows)
        except Exception as e:
            logging.error(f"Cycle failed: {e}")

    def run(self) -> None:
        monitor_cfg = self.config.get('docker_monitor', {})
        run_periodically = monitor_cfg.get('run_periodically', True)
        period_seconds = int(monitor_cfg.get('period_seconds', 300))

        logging.info(
            f"Starting Docker monitor - Periodic: {run_periodically}, Period: {period_seconds}s"
        )

        if not run_periodically:
            self.run_single_cycle()
            return

        while True:
            start = time.time()
            self.run_single_cycle()
            elapsed = time.time() - start
            sleep_for = max(0, period_seconds - int(elapsed))
            time.sleep(sleep_for)


if __name__ == "__main__":
    monitor = DockerMonitor()
    monitor.run()
