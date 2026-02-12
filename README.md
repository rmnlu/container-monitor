# Docker Container Monitor - Enhanced

An enhanced Docker container monitoring solution that collects container metrics and stores them in PostgreSQL for visualization in Grafana.

## ✨ Features

1. **Regex Pattern Filtering**
   - Separate include/exclude patterns for container names
   - Separate include/exclude patterns for image names
   - Monitor all containers by default (only filter when patterns are specified)
   - Flexible filtering logic with exclude patterns taking priority

2. **Enhanced Metrics**
   - **Restart counts**: Track how many times each container has restarted
   - **Disk usage**: Monitor disk space consumed by each container
   - Additional size metrics (RW layer, root filesystem)

3. **Better Grafana Integration**
   - Pre-built dashboard with 10 panels
   - Optimized database views for common queries
   - Indexes for improved query performance
   - Time-series tracking of all metrics

## 📋 Prerequisites

- Python 3.7+
- Docker installed on the host
- PostgreSQL 12+
- Grafana (for visualization)

## 🚀 Installation

### 1. Clone or copy the enhanced monitor files

```bash
mkdir -p /opt/docker_monitor
cd /opt/docker_monitor
# Copy all files from docker_monitor_enhanced/
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up the database

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database and user
CREATE DATABASE docker_monitoring;
CREATE USER monitor_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE docker_monitoring TO monitor_user;

# Connect to the database and run migration
\c docker_monitoring
\i migrations/001_add_metrics.sql
```

### 4. Configure the monitor

Edit `config/config.yaml`:

```yaml
database:
  host: localhost
  port: 5432
  database: docker_monitoring
  username: monitor_user
  password: your_secure_password

docker_monitor:
  log_file: logs/docker_monitor.log
  level: INFO
  run_periodically: true
  period_seconds: 300  # 5 minutes

  filters:
    # Example: Monitor only production containers
    include_container_names:
      - "^prod-.*"
    
    # Example: Exclude test containers
    exclude_container_names:
      - ".*-test$"
      - "^dev-.*"
```

### 5. Run the monitor

```bash
# Test run (single cycle)
python docker_monitor.py

# Run continuously (using the config setting)
python docker_monitor.py

# Or run as a systemd service (see below)
```

## 🎯 Regex Pattern Examples

### Container Name Patterns

```yaml
filters:
  # Monitor only production containers
  include_container_names:
    - "^prod-.*"           # Starts with 'prod-'
  
  # Exclude temporary and test containers
  exclude_container_names:
    - "^temp-.*"           # Starts with 'temp-'
    - ".*-test$"           # Ends with '-test'
    - "^dev-.*"            # Development containers
```

### Image Name Patterns

```yaml
filters:
  # Monitor only specific images
  include_image_names:
    - "^nginx:.*"          # All nginx versions
    - "^postgres:1[2-5]"   # PostgreSQL 12-15
    - "myregistry\.com/.*" # Images from private registry
  
  # Exclude debug and alpine images
  exclude_image_names:
    - ".*:alpine$"         # Alpine-based images
    - ".*-debug"           # Debug images
```

### Combined Filtering

```yaml
filters:
  # Only production web containers
  include_container_names:
    - "^prod-web-.*"
  
  # Using official images only
  include_image_names:
    - "^(nginx|node|python):.*"
  
  # Exclude any test images
  exclude_image_names:
    - ".*-test$"
```

## 🔧 Systemd Service Setup

Create `/etc/systemd/system/docker-monitor.service`:

```ini
[Unit]
Description=Docker Container Monitor
After=network.target docker.service postgresql.service
Requires=docker.service

[Service]
Type=simple
User=monitor
Group=monitor
WorkingDirectory=/opt/docker_monitor
ExecStart=/usr/bin/python3 /opt/docker_monitor/docker_monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable docker-monitor
sudo systemctl start docker-monitor
sudo systemctl status docker-monitor
```

## 📊 Grafana Dashboard Setup

### 1. Configure PostgreSQL Data Source

In Grafana:
1. Go to Configuration → Data Sources
2. Add PostgreSQL data source
3. Configure connection:
   - Host: `localhost:5432`
   - Database: `docker_monitoring`
   - User: `monitor_user`
   - Password: `your_password`
   - SSL Mode: `disable` (or configure as needed)

### 2. Import Dashboard

1. Go to Dashboards → Import
2. Upload `grafana_dashboard.json`
3. Select your PostgreSQL data source
4. Click Import

### 3. Dashboard Panels

The dashboard includes:

- **Overview Stats**
  - Running containers count
  - Stopped containers count
  - Maximum restart count
  - Total disk usage

- **Time Series Charts**
  - Container status over time
  - Disk usage by container
  - Restart count history

- **Tables**
  - Top containers by restart count
  - Top containers by disk usage

## 📈 Database Views

The migration creates several useful views:

### v_latest_container_status
Latest status for each container:
```sql
SELECT * FROM v_latest_container_status 
WHERE hostname = 'prod-server-1';
```

### v_container_status_changes
Containers that changed status or restarted:
```sql
SELECT * FROM v_container_status_changes
WHERE restart_count_delta > 0
ORDER BY current_time DESC;
```

### v_disk_usage_by_host
Aggregated disk usage per host:
```sql
SELECT * FROM v_disk_usage_by_host
ORDER BY snapshot_time DESC
LIMIT 10;
```

### v_high_restart_containers
Containers with restart_count > 5:
```sql
SELECT * FROM v_high_restart_containers
ORDER BY restart_count DESC;
```

## 🔍 Monitoring Tips

### Alert on High Restart Counts

```sql
-- Containers restarted more than 5 times
SELECT container_name, restart_count, status
FROM v_latest_container_status
WHERE restart_count > 5
ORDER BY restart_count DESC;
```

### Track Disk Usage Growth

```sql
-- Disk usage trend for a specific container
SELECT 
    snapshot_time,
    container_name,
    pg_size_pretty(disk_usage_bytes::bigint) as disk_usage
FROM container_status
WHERE container_name = 'prod-web-api'
ORDER BY snapshot_time DESC
LIMIT 20;
```

### Identify Frequently Restarting Containers

```sql
-- Containers that restarted in the last hour
SELECT DISTINCT
    cs1.container_name,
    cs1.restart_count - COALESCE(cs2.restart_count, 0) as restarts_last_hour
FROM container_status cs1
LEFT JOIN container_status cs2 
    ON cs1.container_id = cs2.container_id
    AND cs2.snapshot_time = (
        SELECT MAX(snapshot_time)
        FROM container_status cs3
        WHERE cs3.container_id = cs1.container_id
        AND cs3.snapshot_time < NOW() - INTERVAL '1 hour'
    )
WHERE cs1.snapshot_time >= NOW() - INTERVAL '1 hour'
AND (cs1.restart_count - COALESCE(cs2.restart_count, 0)) > 0
ORDER BY restarts_last_hour DESC;
```

## 📝 Configuration Reference

### Filter Logic

The monitor applies filters in this order:

1. **No patterns specified**: Monitor all containers
2. **Exclude patterns**: If container matches any exclude pattern, skip it
3. **Include patterns**: If include patterns exist, container must match at least one
4. **Both name and image filters**: Container must pass both checks

### Filter Priorities

- Exclude patterns have **highest priority**
- A container excluded by name cannot be included by image pattern
- If no include patterns are specified, all non-excluded containers are monitored

## 🐛 Troubleshooting

### No containers being monitored

Check your regex patterns:
```bash
# Enable debug logging
# In config.yaml:
docker_monitor:
  level: DEBUG
```

The debug log will show which containers are being filtered and why.

### Disk usage showing as 0

Some Docker versions don't support size reporting. The monitor will:
1. Try `docker inspect` for SizeRw and SizeRootFs
2. Try `docker system df -v`
3. Fall back to 0 if neither works

### High memory usage

If monitoring many containers, consider:
- Increasing the `period_seconds` interval
- Using more restrictive filters
- Running the monitor on a dedicated host

## 📄 License

MIT License - feel free to modify and use as needed.

## 🤝 Contributing

Suggestions and improvements welcome! Key areas for enhancement:
- Additional metrics (network I/O, CPU usage)
- Support for Docker Swarm mode
- Container log parsing and storage
- Alert rule templates

## 📞 Support

For issues or questions:
1. Check the debug logs: `tail -f logs/docker_monitor.log`
2. Verify database connectivity: `psql -U monitor_user -d docker_monitoring`
3. Test Docker access: `docker ps -a`