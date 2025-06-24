# Home Assistant Frameo Control Backend Add-on

This repository contains the backend server for the **[HA Frameo Control](https://github.com/HunorLaczko/ha-frameo-control)** integration. This addon runs as a dedicated service inside Home Assistant, handling all direct communication with your Frameo device via the Android Debug Bridge (ADB) protocol.

**This addon is a required dependency.** You must install and run this addon before setting up the main integration.

## Why is a separate addon required?

Standard Home Assistant custom components have security restrictions that prevent them from directly accessing host USB devices. This addon is necessary because it contains the required system libraries (like `libusb`) and has the appropriate permissions to communicate with the Frameo frame over a USB cable, which is essential for the initial setup and for reliable control.

## Key Features

* **Enables Direct Device Communication:** Provides the critical link between Home Assistant and your Frameo frame.
* **USB & Network Support:** Handles both USB-connected ADB and network (Wireless ADB) connections.
* **Stateful Connection:** Maintains an active connection to the device for responsive control.
* **Simple API:** Exposes a simple API for the frontend integration to send commands (e.g., tap, swipe, start app).

## ⚙️ Installation

1.  Navigate to your Home Assistant instance.
2.  Go to **Settings > Add-ons > Add-on Store**.
3.  Click the three-dots menu (⋮) in the top-right corner and select **Repositories**.
4.  Add the URL of this repository (`https://github.com/HunorLaczko/ha-frameo-control-addon`) and click **Add**.
5.  Close the dialog. The "Frameo Control Backend" addon will now appear in the store.
6.  Click on the new addon and then click **Install**. Wait for the installation to complete.
7.  **Start** the addon.

## 🛠️ Configuration

This addon itself does not require any manual configuration in its "Configuration" tab.

All setup, such as selecting your USB device or entering a network address, is handled visually by the **HA Frameo Control** integration when you add it in Home Assistant. Just ensure the addon is running before you proceed.

## ➡️ Next Steps

Once this addon is installed and running, you are ready to install the frontend:

➡️ **[Install the HA Frameo Control Integration](https://github.com/HunorLaczko/ha-frameo-control)**