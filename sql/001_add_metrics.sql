-- Migration: Add restart_count and disk usage fields to container_status table
-- Enhanced Docker Monitor Schema
-- Version: 2.0

-- Step 1: Add new columns to existing table (if upgrading from v1)
-- If creating fresh, skip to Step 2

DO $$
BEGIN
    -- Add restart_count column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'container_status' 
        AND column_name = 'restart_count'
    ) THEN
        ALTER TABLE container_status 
        ADD COLUMN restart_count INTEGER DEFAULT 0 NOT NULL;
    END IF;

    -- Add disk_usage_bytes column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'container_status' 
        AND column_name = 'disk_usage_bytes'
    ) THEN
        ALTER TABLE container_status 
        ADD COLUMN disk_usage_bytes BIGINT DEFAULT 0 NOT NULL;
    END IF;

    -- Add size_rw_bytes column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'container_status' 
        AND column_name = 'size_rw_bytes'
    ) THEN
        ALTER TABLE container_status 
        ADD COLUMN size_rw_bytes BIGINT DEFAULT 0 NOT NULL;
    END IF;

    -- Add size_root_fs_bytes column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'container_status' 
        AND column_name = 'size_root_fs_bytes'
    ) THEN
        ALTER TABLE container_status 
        ADD COLUMN size_root_fs_bytes BIGINT DEFAULT 0 NOT NULL;
    END IF;
END $$;

-- Step 2: Create fresh table (if not exists)
-- This includes all fields from v1 plus new fields

CREATE TABLE IF NOT EXISTS container_status (
    id SERIAL PRIMARY KEY,
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    container_id VARCHAR(255) NOT NULL,
    container_name VARCHAR(255) NOT NULL,
    image_name VARCHAR(500) NOT NULL,
    container_created_at TIMESTAMP WITH TIME ZONE,
    running_for VARCHAR(100),
    status VARCHAR(50) NOT NULL,
    
    -- Enhanced metrics for Grafana
    restart_count INTEGER DEFAULT 0 NOT NULL,
    disk_usage_bytes BIGINT DEFAULT 0 NOT NULL,
    size_rw_bytes BIGINT DEFAULT 0 NOT NULL,
    size_root_fs_bytes BIGINT DEFAULT 0 NOT NULL,
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Unique constraint for upserts
    CONSTRAINT unique_container UNIQUE (hostname, container_id)
);

-- Step 3: Create indexes for better Grafana query performance

-- Index for time-series queries
CREATE INDEX IF NOT EXISTS idx_container_status_snapshot_time 
ON container_status(snapshot_time DESC);

-- Index for filtering by hostname
CREATE INDEX IF NOT EXISTS idx_container_status_hostname 
ON container_status(hostname);

-- Index for filtering by container name
CREATE INDEX IF NOT EXISTS idx_container_status_container_name 
ON container_status(container_name);

-- Index for filtering by image name
CREATE INDEX IF NOT EXISTS idx_container_status_image_name 
ON container_status(image_name);

-- Index for filtering by status
CREATE INDEX IF NOT EXISTS idx_container_status_status 
ON container_status(status);

-- Composite index for common Grafana queries (hostname + time)
CREATE INDEX IF NOT EXISTS idx_container_status_hostname_time 
ON container_status(hostname, snapshot_time DESC);

-- Composite index for container tracking over time
CREATE INDEX IF NOT EXISTS idx_container_status_container_time 
ON container_status(container_id, snapshot_time DESC);

-- Step 4: Create helpful views for Grafana

-- View: Latest status for each container
CREATE OR REPLACE VIEW v_latest_container_status AS
SELECT DISTINCT ON (hostname, container_id)
    *
FROM container_status
ORDER BY hostname, container_id, snapshot_time DESC;

-- View: Container status changes (for alerting)
CREATE OR REPLACE VIEW v_container_status_changes AS
SELECT 
    cs1.hostname,
    cs1.container_id,
    cs1.container_name,
    cs1.image_name,
    cs1.status as current_status,
    cs1.snapshot_time as current_time,
    cs2.status as previous_status,
    cs2.snapshot_time as previous_time,
    cs1.restart_count - COALESCE(cs2.restart_count, 0) as restart_count_delta
FROM container_status cs1
LEFT JOIN LATERAL (
    SELECT status, snapshot_time, restart_count
    FROM container_status cs2
    WHERE cs2.hostname = cs1.hostname 
    AND cs2.container_id = cs1.container_id
    AND cs2.snapshot_time < cs1.snapshot_time
    ORDER BY cs2.snapshot_time DESC
    LIMIT 1
) cs2 ON true
WHERE cs1.status != COALESCE(cs2.status, cs1.status)
   OR (cs1.restart_count - COALESCE(cs2.restart_count, 0)) > 0;

-- View: Disk usage summary by hostname
CREATE OR REPLACE VIEW v_disk_usage_by_host AS
SELECT 
    hostname,
    snapshot_time,
    COUNT(*) as container_count,
    SUM(disk_usage_bytes) as total_disk_usage_bytes,
    AVG(disk_usage_bytes) as avg_disk_usage_bytes,
    MAX(disk_usage_bytes) as max_disk_usage_bytes
FROM container_status
GROUP BY hostname, snapshot_time
ORDER BY hostname, snapshot_time DESC;

-- View: Containers with high restart counts
CREATE OR REPLACE VIEW v_high_restart_containers AS
SELECT 
    hostname,
    container_id,
    container_name,
    image_name,
    restart_count,
    status,
    snapshot_time
FROM v_latest_container_status
WHERE restart_count > 5
ORDER BY restart_count DESC;

-- Step 5: Add comments for documentation

COMMENT ON TABLE container_status IS 'Docker container monitoring data with metrics for Grafana dashboards';
COMMENT ON COLUMN container_status.restart_count IS 'Number of times the container has been restarted';
COMMENT ON COLUMN container_status.disk_usage_bytes IS 'Disk space used by the container in bytes';
COMMENT ON COLUMN container_status.size_rw_bytes IS 'Size of files written by the container (writable layer)';
COMMENT ON COLUMN container_status.size_root_fs_bytes IS 'Total size of container root filesystem';

COMMENT ON VIEW v_latest_container_status IS 'Most recent status for each container';
COMMENT ON VIEW v_container_status_changes IS 'Containers that have changed status or restarted';
COMMENT ON VIEW v_disk_usage_by_host IS 'Aggregated disk usage statistics per host';
COMMENT ON VIEW v_high_restart_containers IS 'Containers with restart count > 5';

-- Step 6: Grant permissions (adjust username as needed)
-- GRANT SELECT, INSERT, UPDATE ON container_status TO monitor_user;
-- GRANT USAGE, SELECT ON SEQUENCE container_status_id_seq TO monitor_user;
-- GRANT SELECT ON v_latest_container_status TO monitor_user;
-- GRANT SELECT ON v_container_status_changes TO monitor_user;
-- GRANT SELECT ON v_disk_usage_by_host TO monitor_user;
-- GRANT SELECT ON v_high_restart_containers TO monitor_user;

-- Migration complete!
