#!/usr/bin/env python3
"""
Docker Containers Monitor - Enhanced Version

Collects information about running and stopped Docker containers on the host VM
and stores snapshots into the PostgreSQL database configured in config/config.yaml.

Enhanced features:
- Regex patterns for filtering containers by name and image
- Container restart counts
- Disk usage per container
- Additional metrics for Grafana dashboards

Captured fields per container: snapshot_time, hostname, container_id, name,
image, container_created_at, status, restart_count, disk_usage_bytes, etc.
"""

import sys
import os
import json
import yaml
import time
import socket
import logging
import subprocess
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Pattern

import psycopg2
from dateutil import parser as dateutil_parser


class DockerMonitor:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self._setup_logging()
        self._compile_filter_patterns()

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

    def _compile_filter_patterns(self) -> None:
        """Compile regex patterns for container name and image filtering."""
        monitor_cfg = self.config.get('docker_monitor', {})
        filters = monitor_cfg.get('filters')

        # Container name patterns
        self.include_name_patterns: List[Pattern] = []
        self.exclude_name_patterns: List[Pattern] = []
        
        # Image name patterns
        self.include_image_patterns: List[Pattern] = []
        self.exclude_image_patterns: List[Pattern] = []

        # If filters is None or not configured, skip pattern compilation
        if filters is None:
            logging.info("No filters configured - monitoring all containers")
            return

        try:
            # Compile name patterns
            include_names = filters.get('include_container_names') or []
            exclude_names = filters.get('exclude_container_names') or []
            include_images = filters.get('include_image_names') or []
            exclude_images = filters.get('exclude_image_names') or []

            for pattern_str in include_names:
                if pattern_str:  # Skip empty strings
                    self.include_name_patterns.append(re.compile(pattern_str))
            
            for pattern_str in exclude_names:
                if pattern_str:
                    self.exclude_name_patterns.append(re.compile(pattern_str))

            # Compile image patterns
            for pattern_str in include_images:
                if pattern_str:
                    self.include_image_patterns.append(re.compile(pattern_str))
            
            for pattern_str in exclude_images:
                if pattern_str:
                    self.exclude_image_patterns.append(re.compile(pattern_str))

            # Log filter configuration
            total_filters = (len(self.include_name_patterns) + len(self.exclude_name_patterns) +
                           len(self.include_image_patterns) + len(self.exclude_image_patterns))
            
            if total_filters == 0:
                logging.info("No filters configured - monitoring all containers")
            else:
                if self.include_name_patterns or self.exclude_name_patterns:
                    logging.info(f"Container name filters: {len(self.include_name_patterns)} include, {len(self.exclude_name_patterns)} exclude")
                
                if self.include_image_patterns or self.exclude_image_patterns:
                    logging.info(f"Image name filters: {len(self.include_image_patterns)} include, {len(self.exclude_image_patterns)} exclude")

        except re.error as e:
            logging.error(f"Invalid regex pattern in config: {e}")
            sys.exit(1)

    def _should_monitor_container(self, container_name: str, image_name: str) -> bool:
        """
        Determine if a container should be monitored based on regex patterns.
        
        Logic:
        1. If no patterns are specified, monitor all containers
        2. Check exclude patterns first - if matched, skip container
        3. If include patterns exist, only monitor if matched
        4. If only exclude patterns exist, monitor everything except excluded
        """
        has_name_filters = bool(self.include_name_patterns or self.exclude_name_patterns)
        has_image_filters = bool(self.include_image_patterns or self.exclude_image_patterns)
        
        # No filters = monitor all
        if not has_name_filters and not has_image_filters:
            return True

        # Check exclude patterns first (highest priority)
        for pattern in self.exclude_name_patterns:
            if pattern.search(container_name):
                logging.debug(f"Container '{container_name}' excluded by name pattern: {pattern.pattern}")
                return False
        
        for pattern in self.exclude_image_patterns:
            if pattern.search(image_name):
                logging.debug(f"Container '{container_name}' excluded by image pattern: {pattern.pattern}")
                return False

        # If include patterns exist, container must match at least one
        name_match = True
        image_match = True

        if self.include_name_patterns:
            name_match = any(pattern.search(container_name) for pattern in self.include_name_patterns)
        
        if self.include_image_patterns:
            image_match = any(pattern.search(image_name) for pattern in self.include_image_patterns)

        should_monitor = name_match and image_match
        
        if not should_monitor:
            logging.debug(f"Container '{container_name}' ({image_name}) did not match include patterns")
        
        return should_monitor

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

    def _get_container_stats(self, container_id: str) -> Dict[str, Any]:
        """
        Get additional stats for a container using docker inspect.
        Returns restart count and disk usage.
        """
        stats = {
            'restart_count': 0,
            'disk_usage_bytes': 0,
            'size_rw_bytes': 0,
            'size_root_fs_bytes': 0
        }

        try:
            # Get restart count from inspect
            result = subprocess.run(
                ['docker', 'inspect', container_id],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            
            inspect_data = json.loads(result.stdout)
            if inspect_data and len(inspect_data) > 0:
                container_info = inspect_data[0]
                
                # Get restart count
                restart_count = container_info.get('RestartCount', 0)
                stats['restart_count'] = restart_count

                # Get size information if available
                size_rw = container_info.get('SizeRw', 0)
                size_root_fs = container_info.get('SizeRootFs', 0)
                
                stats['size_rw_bytes'] = size_rw if size_rw else 0
                stats['size_root_fs_bytes'] = size_root_fs if size_root_fs else 0

        except subprocess.TimeoutExpired:
            logging.warning(f"Timeout getting stats for container {container_id[:12]}")
        except subprocess.CalledProcessError as e:
            logging.warning(f"Failed to inspect container {container_id[:12]}: {e}")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logging.warning(f"Failed to parse inspect data for {container_id[:12]}: {e}")

        # Try to get disk usage using docker system df
        try:
            result = subprocess.run(
                ['docker', 'system', 'df', '-v', '--format', '{{json .}}'],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )
            
            # Parse each line to find our container
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    df_data = json.loads(line)
                    # Check if this is a container entry matching our ID
                    if df_data.get('Type') == 'container' and container_id.startswith(df_data.get('ID', '')[:12]):
                        size_str = df_data.get('Size', '0B')
                        stats['disk_usage_bytes'] = self._parse_size_string(size_str)
                        break
                except json.JSONDecodeError:
                    continue

        except subprocess.TimeoutExpired:
            logging.warning(f"Timeout getting disk usage for container {container_id[:12]}")
        except subprocess.CalledProcessError:
            # docker system df might not support -v flag in older versions
            # Fall back to SizeRootFs if available
            if stats['size_root_fs_bytes'] > 0:
                stats['disk_usage_bytes'] = stats['size_root_fs_bytes']

        return stats

    def _parse_size_string(self, size_str: str) -> int:
        """Convert size string like '1.5GB' or '256MB' to bytes."""
        size_str = size_str.strip().upper()
        
        multipliers = {
            'B': 1,
            'KB': 1024,
            'MB': 1024 ** 2,
            'GB': 1024 ** 3,
            'TB': 1024 ** 4,
            'K': 1024,
            'M': 1024 ** 2,
            'G': 1024 ** 3,
            'T': 1024 ** 4,
        }
        
        for suffix, multiplier in multipliers.items():
            if size_str.endswith(suffix):
                try:
                    number = float(size_str[:-len(suffix)])
                    return int(number * multiplier)
                except ValueError:
                    return 0
        
        # Try to parse as plain number
        try:
            return int(float(size_str))
        except ValueError:
            return 0

    def _parse_created_at(self, created_at_str: str) -> Optional[datetime]:
        if not created_at_str:
            return None
        try:
            dt = dateutil_parser.parse(created_at_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def collect_snapshot(self) -> List[Dict[str, Any]]:
        hostname = socket.gethostname()
        snapshot_time = datetime.now(timezone.utc)
        raw = self._run_docker_ps()

        snapshot: List[Dict[str, Any]] = []
        filtered_count = 0
        
        for c in raw:
            container_name = c.get('Names') or ''
            image_name = c.get('Image') or ''
            
            # Apply filters
            if not self._should_monitor_container(container_name, image_name):
                filtered_count += 1
                continue

            container_id = c.get('ID') or ''
            
            # Get additional stats (restart count, disk usage)
            stats = self._get_container_stats(container_id)

            container = {
                'snapshot_time': snapshot_time,
                'hostname': hostname,
                'container_id': container_id,
                'container_name': container_name,
                'image_name': image_name,
                'container_created_at': self._parse_created_at(c.get('CreatedAt') or ''),
                'running_for': c.get('RunningFor') or '',
                'status': c.get('State') or '',
                'restart_count': stats['restart_count'],
                'disk_usage_bytes': stats['disk_usage_bytes'],
                'size_rw_bytes': stats['size_rw_bytes'],
                'size_root_fs_bytes': stats['size_root_fs_bytes'],
            }
            snapshot.append(container)
        
        if filtered_count > 0:
            logging.info(f"Filtered out {filtered_count} containers based on regex patterns")
        
        logging.info(f"Collected data for {len(snapshot)} containers")
        
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
                    image_name, container_created_at, running_for, status,
                    restart_count, disk_usage_bytes, size_rw_bytes, size_root_fs_bytes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (hostname, container_id)
                DO UPDATE SET
                    snapshot_time = EXCLUDED.snapshot_time,
                    container_name = EXCLUDED.container_name,
                    image_name = EXCLUDED.image_name,
                    container_created_at = EXCLUDED.container_created_at,
                    running_for = EXCLUDED.running_for,
                    status = EXCLUDED.status,
                    restart_count = EXCLUDED.restart_count,
                    disk_usage_bytes = EXCLUDED.disk_usage_bytes,
                    size_rw_bytes = EXCLUDED.size_rw_bytes,
                    size_root_fs_bytes = EXCLUDED.size_root_fs_bytes,
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
                    r['restart_count'],
                    r['disk_usage_bytes'],
                    r['size_rw_bytes'],
                    r['size_root_fs_bytes'],
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
