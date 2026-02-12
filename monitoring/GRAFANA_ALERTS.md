# Grafana Alert Queries for Docker Monitor

This file contains example alert queries you can use in Grafana to monitor container health.

## Alert 1: High Restart Count

**Alert Name**: Container High Restart Count
**Severity**: Warning
**Trigger**: Container has restarted more than 5 times

```sql
SELECT 
    container_name,
    restart_count,
    status,
    hostname
FROM v_latest_container_status
WHERE restart_count > 5
ORDER BY restart_count DESC
```

**Grafana Alert Rule**:
- Condition: `WHEN count() OF query(A, 1m, now) IS ABOVE 0`
- For: 5m
- Annotations:
  - Summary: "Container {{ $labels.container_name }} has high restart count"
  - Description: "Container {{ $labels.container_name }} on {{ $labels.hostname }} has restarted {{ $value }} times"

---

## Alert 2: Container Stopped Unexpectedly

**Alert Name**: Container Stopped
**Severity**: Critical
**Trigger**: Container that was running is now stopped

```sql
SELECT 
    container_name,
    current_status,
    previous_status,
    hostname,
    EXTRACT(EPOCH FROM (current_time - previous_time)) as seconds_since_stop
FROM v_container_status_changes
WHERE previous_status = 'running'
  AND current_status != 'running'
  AND current_time > NOW() - INTERVAL '10 minutes'
ORDER BY current_time DESC
```

**Grafana Alert Rule**:
- Condition: `WHEN count() OF query(A, 5m, now) IS ABOVE 0`
- For: 1m
- Annotations:
  - Summary: "Container {{ $labels.container_name }} stopped"
  - Description: "Container {{ $labels.container_name }} on {{ $labels.hostname }} changed from {{ $labels.previous_status }} to {{ $labels.current_status }}"

---

## Alert 3: High Disk Usage per Container

**Alert Name**: Container High Disk Usage
**Severity**: Warning
**Trigger**: Container using more than 10GB of disk space

```sql
SELECT 
    container_name,
    image_name,
    hostname,
    pg_size_pretty(disk_usage_bytes::bigint) as disk_usage,
    disk_usage_bytes
FROM v_latest_container_status
WHERE disk_usage_bytes > 10737418240  -- 10GB in bytes
ORDER BY disk_usage_bytes DESC
```

**Grafana Alert Rule**:
- Condition: `WHEN max() OF query(A, 5m, now) IS ABOVE 10737418240`
- For: 10m
- Annotations:
  - Summary: "Container {{ $labels.container_name }} high disk usage"
  - Description: "Container {{ $labels.container_name }} is using {{ $value | humanize1024 }}B of disk space"

---

## Alert 4: Total Host Disk Usage

**Alert Name**: Host Total Container Disk Usage High
**Severity**: Warning
**Trigger**: All containers on a host using more than 50GB total

```sql
SELECT 
    hostname,
    total_disk_usage_bytes,
    container_count,
    pg_size_pretty(total_disk_usage_bytes::bigint) as total_usage
FROM v_disk_usage_by_host
WHERE snapshot_time = (
    SELECT MAX(snapshot_time) FROM v_disk_usage_by_host
)
AND total_disk_usage_bytes > 53687091200  -- 50GB
ORDER BY total_disk_usage_bytes DESC
```

**Grafana Alert Rule**:
- Condition: `WHEN max() OF query(A, 5m, now) IS ABOVE 53687091200`
- For: 15m
- Annotations:
  - Summary: "High total container disk usage on {{ $labels.hostname }}"
  - Description: "Total disk usage on {{ $labels.hostname }}: {{ $value | humanize1024 }}B across {{ $labels.container_count }} containers"

---

## Alert 5: Container Restarted Recently

**Alert Name**: Container Recently Restarted
**Severity**: Info
**Trigger**: Container restart count increased in the last monitoring cycle

```sql
SELECT 
    container_name,
    current_status,
    restart_count_delta,
    hostname
FROM v_container_status_changes
WHERE restart_count_delta > 0
  AND current_time > NOW() - INTERVAL '10 minutes'
ORDER BY restart_count_delta DESC, current_time DESC
```

**Grafana Alert Rule**:
- Condition: `WHEN count() OF query(A, 5m, now) IS ABOVE 0`
- For: 1m
- Annotations:
  - Summary: "Container {{ $labels.container_name }} restarted"
  - Description: "Container {{ $labels.container_name }} on {{ $labels.hostname }} restarted {{ $value }} times in the last cycle"

---

## Alert 6: No Monitoring Data

**Alert Name**: Docker Monitor Not Reporting
**Severity**: Critical
**Trigger**: No data received in the last 15 minutes

```sql
SELECT 
    MAX(snapshot_time) as last_snapshot,
    EXTRACT(EPOCH FROM (NOW() - MAX(snapshot_time))) as seconds_since_last_snapshot
FROM container_status
HAVING EXTRACT(EPOCH FROM (NOW() - MAX(snapshot_time))) > 900  -- 15 minutes
```

**Grafana Alert Rule**:
- Condition: `WHEN max() OF query(A, 5m, now) IS ABOVE 900`
- For: 5m
- Annotations:
  - Summary: "Docker monitor not reporting data"
  - Description: "No container monitoring data received for {{ $value }} seconds"

---

## Alert 7: Disk Usage Growing Rapidly

**Alert Name**: Rapid Disk Usage Growth
**Severity**: Warning
**Trigger**: Container disk usage increased by more than 1GB in 1 hour

```sql
WITH current AS (
    SELECT DISTINCT ON (container_id)
        container_id,
        container_name,
        hostname,
        disk_usage_bytes as current_usage,
        snapshot_time as current_time
    FROM container_status
    WHERE snapshot_time >= NOW() - INTERVAL '10 minutes'
    ORDER BY container_id, snapshot_time DESC
),
previous AS (
    SELECT DISTINCT ON (container_id)
        container_id,
        disk_usage_bytes as previous_usage,
        snapshot_time as previous_time
    FROM container_status
    WHERE snapshot_time BETWEEN NOW() - INTERVAL '70 minutes' AND NOW() - INTERVAL '50 minutes'
    ORDER BY container_id, snapshot_time DESC
)
SELECT 
    c.container_name,
    c.hostname,
    pg_size_pretty(c.current_usage::bigint) as current_usage,
    pg_size_pretty(p.previous_usage::bigint) as previous_usage,
    pg_size_pretty((c.current_usage - p.previous_usage)::bigint) as growth,
    (c.current_usage - p.previous_usage) as growth_bytes
FROM current c
JOIN previous p ON c.container_id = p.container_id
WHERE (c.current_usage - p.previous_usage) > 1073741824  -- 1GB growth
ORDER BY growth_bytes DESC
```

**Grafana Alert Rule**:
- Condition: `WHEN max() OF query(A, 5m, now) IS ABOVE 1073741824`
- For: 5m
- Annotations:
  - Summary: "Rapid disk usage growth in {{ $labels.container_name }}"
  - Description: "Container {{ $labels.container_name }} grew by {{ $value | humanize1024 }}B in the last hour"

---

## Setting Up Alerts in Grafana

### Method 1: Alert Rules (Grafana 8+)

1. Go to **Alerting** → **Alert rules**
2. Click **New alert rule**
3. Set query from examples above
4. Configure conditions and thresholds
5. Add notification channel
6. Save the rule

### Method 2: Panel Alerts (Grafana 7)

1. Edit a dashboard panel
2. Go to **Alert** tab
3. Click **Create Alert**
4. Configure conditions
5. Add notification channel
6. Save the panel

### Notification Channels

Configure in **Alerting** → **Notification channels**:

- **Slack**: Team notifications
- **PagerDuty**: On-call escalation
- **Email**: Alert summaries
- **Webhook**: Custom integrations

---

## Alert Best Practices

1. **Start with warnings**: Don't alert on everything immediately
2. **Use appropriate time windows**: Balance responsiveness vs noise
3. **Group related alerts**: Combine container-level alerts by host
4. **Set up alert dependencies**: Don't alert on containers if host is down
5. **Document resolution steps**: Include runbook links in annotations
6. **Test alerts regularly**: Verify notification delivery
7. **Review and tune**: Adjust thresholds based on actual behavior

---

## Customizing Thresholds

Adjust these values based on your environment:

- **Restart count**: Default 5, adjust for flaky apps
- **Disk usage**: Default 10GB, adjust for container size
- **Time windows**: Default 5-15min, adjust for monitoring frequency
- **Growth rate**: Default 1GB/hour, adjust for expected patterns
