# HA Frameo Control Backend Add-on

This is the backend server component for the **HA Frameo Control** integration. It runs as a dedicated Home Assistant Add-on to handle all direct communication with your Frameo device via the Android Debug Bridge (ADB) protocol.

This add-on is required for the main integration to work. It contains the necessary system libraries (`libusb`) to communicate with the Frameo frame over a USB cable - a feature that is not possible in a standard custom component.

---

## ⚙️ Installation

1. Navigate to the Home Assistant Add-on store.
2. Click the three-dots menu (⋮) in the top right and select **Repositories**.
3. Add the URL of this repository.
4. The "Frameo Control Backend" will appear. Click on it and then click **Install**.
5. Once installed, move to the **Configuration** tab to set up your device connection.

---

## 🛠️ Configuration

Before starting the add-on, you must configure how it will connect to your Frameo device.

- **Connection Type:**
  - **USB (Default):** Connect directly to a USB port on your Home Assistant machine. This is the recommended method for initial setup.
  - **Network:** Connect to the device over your network using its IP address. This requires Wireless ADB to be enabled first.
- **Device Serial:** (Required for USB) The unique serial number of your device. See the main integration's documentation for instructions on how to find this.
- **Device Host / Port:** (Required for Network) The IP address and port (usually 5555) of your Frameo device.

After saving the configuration, you can start the add-on from the **Info** tab. Check the **Log** tab to ensure it starts without errors.

---

## ➡️ Next Steps

Once this add-on is installed, configured, and running, you can proceed to install the **HA Frameo Control Frontend Integration** to add the entities to Home Assistant.