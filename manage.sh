#!/bin/bash
#
# Docker Monitor Management Script
# Manage the docker-monitor systemd service
#

set -e

SERVICE_NAME="docker-monitor"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This command must be run as root (use 'sudo')"
        exit 1
    fi
}

# Start the service
start_service() {
    print_info "Starting $SERVICE_NAME..."
    
    if systemctl start "$SERVICE_NAME"; then
        print_success "$SERVICE_NAME started successfully"
        
        # Wait a moment and check status
        sleep 1
        systemctl status "$SERVICE_NAME" --no-pager
    else
        print_error "Failed to start $SERVICE_NAME"
        exit 1
    fi
}

# Stop the service
stop_service() {
    print_info "Stopping $SERVICE_NAME..."
    
    if systemctl stop "$SERVICE_NAME"; then
        print_success "$SERVICE_NAME stopped successfully"
    else
        print_error "Failed to stop $SERVICE_NAME"
        exit 1
    fi
}

# Restart the service
restart_service() {
    print_info "Restarting $SERVICE_NAME..."
    
    if systemctl restart "$SERVICE_NAME"; then
        print_success "$SERVICE_NAME restarted successfully"
        
        # Wait a moment and check status
        sleep 1
        systemctl status "$SERVICE_NAME" --no-pager
    else
        print_error "Failed to restart $SERVICE_NAME"
        exit 1
    fi
}

# Check service status
status_service() {
    print_info "Checking $SERVICE_NAME status..."
    echo ""
    systemctl status "$SERVICE_NAME" --no-pager
}

# Show service logs
show_logs() {
    local lines="${1:-50}"
    print_info "Showing last $lines lines of $SERVICE_NAME logs..."
    echo ""
    journalctl -u "$SERVICE_NAME" -n "$lines" --no-pager
}

# Follow service logs in real-time
follow_logs() {
    print_info "Following $SERVICE_NAME logs (press Ctrl+C to stop)..."
    echo ""
    journalctl -u "$SERVICE_NAME" -f --no-pager
}

# Enable service to start on boot
enable_service() {
    print_info "Enabling $SERVICE_NAME to start on boot..."
    
    if systemctl enable "$SERVICE_NAME"; then
        print_success "$SERVICE_NAME enabled for auto-start on boot"
    else
        print_error "Failed to enable $SERVICE_NAME"
        exit 1
    fi
}

# Disable service from starting on boot
disable_service() {
    print_info "Disabling $SERVICE_NAME auto-start on boot..."
    
    if systemctl disable "$SERVICE_NAME"; then
        print_success "$SERVICE_NAME disabled for auto-start on boot"
    else
        print_error "Failed to disable $SERVICE_NAME"
        exit 1
    fi
}

# Reload daemon and service files
reload_daemon() {
    print_info "Reloading systemd daemon..."
    
    if systemctl daemon-reload; then
        print_success "Systemd daemon reloaded"
    else
        print_error "Failed to reload systemd daemon"
        exit 1
    fi
}

# Show usage information
show_usage() {
    cat << EOF
${BLUE}Docker Monitor Management Script${NC}

Usage: $0 <command> [options]

Commands:
    start       Start the docker-monitor service
    stop        Stop the docker-monitor service
    restart     Restart the docker-monitor service
    status      Show service status
    logs        Show last 50 lines of logs (use 'logs <N>' for N lines)
    follow      Follow logs in real-time (press Ctrl+C to stop)
    enable      Enable auto-start on boot
    disable     Disable auto-start on boot
    reload      Reload systemd daemon (use after editing service file)

Examples:
    $0 start              # Start the monitor
    $0 stop               # Stop the monitor
    $0 status             # Check if running
    $0 logs 100           # Show last 100 log lines
    $0 follow             # Watch logs in real-time

Notes:
    - Most commands require root/sudo privileges
    - Service name: $SERVICE_NAME
    - Service file: /etc/systemd/system/$SERVICE_NAME.service
    - Working directory: /opt/docker_monitor
    - Log output: systemd journal (journalctl)

EOF
}

# Main script logic
main() {
    local command="${1:-}"
    
    case "$command" in
        start)
            check_root
            start_service
            ;;
        stop)
            check_root
            stop_service
            ;;
        restart)
            check_root
            restart_service
            ;;
        status)
            status_service
            ;;
        logs)
            local lines="${2:-50}"
            show_logs "$lines"
            ;;
        follow)
            follow_logs
            ;;
        enable)
            check_root
            enable_service
            ;;
        disable)
            check_root
            disable_service
            ;;
        reload)
            check_root
            reload_daemon
            ;;
        ""|help|-h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
