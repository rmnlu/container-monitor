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

from sqlite3 import connect
import ssl
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

    def _db_connect(self, no_ssl=True):
        db_cfg = self.config['database']
        connect_kwargs = {
            'host': db_cfg['host'],
            'port': db_cfg['port'],
            'dbname': db_cfg['database'],
            'user': db_cfg['username'],
            'password': db_cfg['password']
        }
        if no_ssl:
            connect_kwargs['sslmode'] = 'disable'
        return psycopg2.connect(**connect_kwargs)

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

        container_short_id = container_id[:12]

        # Method 1: Get restart count and size info from docker inspect
        try:
            result = subprocess.run(
                ['docker', 'inspect', container_id],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )
            
            inspect_data = json.loads(result.stdout)
            if inspect_data and len(inspect_data) > 0:
                container_info = inspect_data[0]
                
                # Get restart count
                restart_count = container_info.get('RestartCount', 0)
                stats['restart_count'] = restart_count
                
                logging.debug(f"Container {container_short_id}: restart_count={restart_count}")

                # Get size information if available
                size_rw = container_info.get('SizeRw')
                size_root_fs = container_info.get('SizeRootFs')
                
                if size_rw is not None:
                    stats['size_rw_bytes'] = size_rw
                    logging.debug(f"Container {container_short_id}: size_rw_bytes={size_rw}")
                
                if size_root_fs is not None:
                    stats['size_root_fs_bytes'] = size_root_fs
                    logging.debug(f"Container {container_short_id}: size_root_fs_bytes={size_root_fs}")

        except subprocess.TimeoutExpired:
            logging.warning(f"Timeout getting inspect data for container {container_short_id}")
        except subprocess.CalledProcessError as e:
            logging.warning(f"Failed to inspect container {container_short_id}: {e.stderr.strip() if e.stderr else e}")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logging.warning(f"Failed to parse inspect data for {container_short_id}: {e}")

        # Method 2: Try docker ps with size flag to get disk usage
        # This is more reliable than docker system df
        try:
            result = subprocess.run(
                ['docker', 'ps', '-a', '--size', '--filter', f'id={container_id}', 
                 '--format', '{{.Size}}'],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )
            
            size_output = result.stdout.strip()
            if size_output:
                # Size output format: "2B (virtual 133MB)" or "1.09kB (virtual 1.09GB)"
                # We want the first number (actual disk usage)
                # Parse formats like: "0B", "1.5GB", "256MB (virtual 1GB)"
                size_parts = size_output.split('(')[0].strip()

                # Prefer the reported actual size if it's non-zero
                disk_bytes = 0
                if size_parts and size_parts != '0B':
                    disk_bytes = self._parse_size_string(size_parts)
                    if disk_bytes > 0:
                        stats['disk_usage_bytes'] = disk_bytes
                        logging.debug(f"Container {container_short_id}: disk_usage_bytes={disk_bytes} (from docker ps --size)")
                else:
                    # If actual size is 0B, try using the virtual size reported in parentheses
                    if '(' in size_output:
                        inside = size_output.split('(', 1)[1].rsplit(')', 1)[0].strip()
                        # inside is often like 'virtual 25.7MB' or just '25.7MB'
                        if inside.lower().startswith('virtual '):
                            virtual_part = inside[8:].strip()
                        else:
                            virtual_part = inside

                        if virtual_part:
                            virtual_bytes = self._parse_size_string(virtual_part)
                            if virtual_bytes > 0:
                                stats['disk_usage_bytes'] = virtual_bytes
                                logging.debug(f"Container {container_short_id}: disk_usage_bytes={virtual_bytes} (from docker ps --size virtual)")
                
        except subprocess.TimeoutExpired:
            logging.debug(f"Timeout getting size via docker ps for {container_short_id}")
        except subprocess.CalledProcessError as e:
            logging.debug(f"docker ps --size failed for {container_short_id}: {e.stderr.strip() if e.stderr else e}")
        except Exception as e:
            logging.debug(f"Error parsing size from docker ps for {container_short_id}: {e}")

        # Method 3: Fallback to size_root_fs if we still don't have disk_usage
        if stats['disk_usage_bytes'] == 0 and stats['size_root_fs_bytes'] > 0:
            stats['disk_usage_bytes'] = stats['size_root_fs_bytes']
            logging.debug(f"Container {container_short_id}: Using size_root_fs as disk_usage")

        # Method 4: Last resort - try docker system df (slowest, but comprehensive)
        # Only use this if we still have no data and it's enabled
        if stats['disk_usage_bytes'] == 0:
            monitor_cfg = self.config.get('docker_monitor', {})
            use_system_df = monitor_cfg.get('use_system_df_fallback', False)
            
            if use_system_df:
                try:
                    result = subprocess.run(
                        ['docker', 'system', 'df', '-v'],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=30
                    )
                    
                    # Parse the output looking for our container
                    for line in result.stdout.splitlines():
                        if container_short_id in line or container_id in line:
                            # Try to extract size from the line
                            # Format varies, but usually has size like "1.5GB" somewhere
                            parts = line.split()
                            for part in parts:
                                if any(part.endswith(suffix) for suffix in ['B', 'KB', 'MB', 'GB', 'TB']):
                                    try:
                                        size_bytes = self._parse_size_string(part)
                                        if size_bytes > 0:
                                            stats['disk_usage_bytes'] = size_bytes
                                            logging.debug(f"Container {container_short_id}: disk_usage_bytes={size_bytes} (from system df)")
                                            break
                                    except:
                                        continue
                            break
                
                except subprocess.TimeoutExpired:
                    logging.warning(f"Timeout running docker system df (consider disabling use_system_df_fallback)")
                except subprocess.CalledProcessError:
                    logging.debug(f"docker system df not available or failed")

        # Log final stats for this container
        if stats['disk_usage_bytes'] > 0 or stats['restart_count'] > 0:
            logging.debug(f"Container {container_short_id} final stats: {stats}")

        return stats

    def _parse_size_string(self, size_str: str) -> int:
        """
        Convert size string like '1.5GB' or '256MB' to bytes.
        Handles formats from docker ps --size and docker system df.
        """
        if not size_str:
            return 0
            
        size_str = size_str.strip().upper()
        
        # Remove any parenthetical content and extra whitespace
        size_str = size_str.split('(')[0].strip()
        
        # Multipliers for different units
        multipliers = {
            'B': 1,
            'KB': 1024,
            'MB': 1024 ** 2,
            'GB': 1024 ** 3,
            'TB': 1024 ** 4,
            'KIB': 1024,
            'MIB': 1024 ** 2,
            'GIB': 1024 ** 3,
            'TIB': 1024 ** 4,
            'K': 1024,
            'M': 1024 ** 2,
            'G': 1024 ** 3,
            'T': 1024 ** 4,
        }
        
        # Try each suffix
        for suffix, multiplier in multipliers.items():
            if size_str.endswith(suffix):
                try:
                    number_str = size_str[:-len(suffix)].strip()
                    number = float(number_str)
                    return int(number * multiplier)
                except ValueError:
                    continue
        
        # Try to parse as plain number (assume bytes)
        try:
            return int(float(size_str))
        except ValueError:
            logging.debug(f"Unable to parse size string: {size_str}")
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
            # connect with no ssl 
            conn = self._db_connect(no_ssl=True)

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