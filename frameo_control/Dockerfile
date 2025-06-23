# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system dependencies including python, pip, and most importantly libusb-dev
RUN apk add --no-cache python3 py3-pip libusb-dev

# Copy root filesystem into the container
COPY rootfs/ /

# Make all shell scripts in the init and services directories executable
RUN find /etc/cont-init.d/ -type f -exec chmod +x {} \;
RUN find /etc/services.d/ -type f -exec chmod +x {} \;

