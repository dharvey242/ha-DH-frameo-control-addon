#!/usr/bin/with-contenv bashio

# Install required Python packages
if bashio::fs.file_exists "/requirements.txt"; then
    bashio::log.info "Installing Python packages from /requirements.txt..."
    pip install --no-cache-dir --break-system-packages -r /requirements.txt
fi
