# Use a Home Assistant base image for addons
FROM ghcr.io/home-assistant/base:stable

# Install system dependencies including python, pip, and most importantly libusb
RUN apk add --no-cache python3 py3-pip libusb-dev

# Set up the working directory
WORKDIR /app

# Copy the Python application and install requirements
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# Make the startup script executable
RUN chmod +x run.sh

# Run the startup script when the container starts
CMD [ "./run.sh" ]