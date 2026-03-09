#!/bin/bash
# Stop all portfolio-daily-tracker services

echo "Stopping services..."
pkill -f "uvicorn backend.main" 2>/dev/null && echo "  Stopped backend" || echo "  Backend not running"
pkill -f "vite" 2>/dev/null && echo "  Stopped frontend" || echo "  Frontend not running"
echo "Done."
