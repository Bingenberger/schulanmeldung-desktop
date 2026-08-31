#!/bin/bash

# Exit on error
set -e

echo "Installing SL-Office Service..."

# Ensure we are in the right directory
cd "$(dirname "$0")"

# Install dependencies including gunicorn
echo "Installing dependencies..."
./venv/bin/pip install -r requirements.txt

# Copy service file
echo "Copying service file..."
sudo cp sl-office.service /etc/systemd/system/

# Reload systemd
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable service
echo "Enabling service..."
sudo systemctl enable sl-office

# Start service
echo "Starting service..."
sudo systemctl restart sl-office

echo "Installation complete!"
echo "Check status with: sudo systemctl status sl-office"
