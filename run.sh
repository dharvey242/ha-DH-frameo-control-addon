#!/usr/bin/with-contenv bashio

# Exit on error
set -e

# Read configuration from the add-on options using bashio
export CONNECTION_TYPE=$(bashio::config 'connection_type')
export DEVICE_SERIAL=$(bashio::config 'device_serial')
export DEVICE_HOST=$(bashio::config 'device_host')
export DEVICE_PORT=$(bashio::config 'device_port')

# Log the configuration for debugging
bashio::log.info "Starting Frameo Control Backend..."
bashio::log.info "Connection Type: ${CONNECTION_TYPE}"

if [ "${CONNECTION_TYPE}" == "USB" ]; then
    bashio::log.info "Device Serial: ${DEVICE_SERIAL:-'(any)'}"
else
    bashio::log.info "Device Host: ${DEVICE_HOST}:${DEVICE_PORT}"
fi

# Start the Python web server
python3 /app/app.py
