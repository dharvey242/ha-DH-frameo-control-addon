import os
import asyncio
import logging
import re
from functools import partial
from quart import Quart, jsonify, request
import usb1

from adb_shell.adb_device import AdbDevice as AdbDeviceSync
from adb_shell.adb_device_async import AdbDeviceUsbAsync, AdbDeviceTcpAsync
from adb_shell.transport.usb_transport import UsbTransport
from adb_shell.exceptions import AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError, DeviceNotFoundError
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
_LOGGER = logging.getLogger(__name__)

# --- Global Signer and Client---
signer = None
adb_client = None

# --- Helper Functions & Classes ---
def _load_or_generate_keys():
    """Load ADB keys from /data/adbkey, or generate them if they don't exist."""
    adb_key_path = "/data/adbkey"
    if not os.path.exists(adb_key_path):
        _LOGGER.info("No ADB key found, generating a new one at %s", adb_key_path)
        keygen(adb_key_path)
    _LOGGER.info("Loading ADB key from %s", adb_key_path)
    with open(adb_key_path) as f:
        priv = f.read()
    with open(adb_key_path + ".pub") as f:
        pub = f.read()
    return PythonRSASigner(pub, priv)

async def _run_sync(func, *args, **kwargs):
    """Run a synchronous (blocking) function in an executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

class AdbClient:
    """A stateful wrapper to manage a persistent ADB connection."""
    def __init__(self):
        self._lock = asyncio.Lock()
        self.device = None # The async device object
        self.conn_details = {} # Store details for reconnects or special commands

    def _auth_callback(self, device_client):
        """Log a message when auth is needed."""
        _LOGGER.info("!!!!!! ACTION REQUIRED !!!!!! Please check your device's screen to 'Allow USB Debugging'.")

    async def connect(self, conn_details: dict):
        """Establish and maintain the connection to the device."""
        async with self._lock:
            if self.device and self.device.available:
                _LOGGER.info("Request to connect, but device is already connected.")
                return {"status": "already_connected"}, 200

            self.conn_details = conn_details
            conn_type = self.conn_details.get("connection_type", "USB").upper()
            _LOGGER.info(f"Attempting to connect via {conn_type} with details: {self.conn_details}")

            try:
                if conn_type == "USB":
                    serial = self.conn_details.get("serial")
                    if not serial:
                        return {"error": "USB connection requires a serial number."}, 400
                    self.device = AdbDeviceUsbAsync(serial=serial, default_transport_timeout_s=9.0)
                else: # NETWORK
                    host = self.conn_details.get("host")
                    port = int(self.conn_details.get("port", 5555))
                    if not host:
                        return {"error": "Network connection requires a host."}, 400
                    self.device = AdbDeviceTcpAsync(host=host, port=port, default_transport_timeout_s=9.0)

                await self.device.connect(rsa_keys=[signer], auth_timeout_s=120.0, auth_callback=self._auth_callback)
                _LOGGER.info(f"Successfully connected to device: {self.conn_details.get('serial') or self.conn_details.get('host')}")
                return {"status": "connected"}, 200

            except (AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError, DeviceNotFoundError, usb1.USBError, ConnectionResetError) as e:
                _LOGGER.error(f"Failed to connect to device: {e}")
                self.device = None
                return {"error": f"Connection failed: {e}"}, 500
            except Exception as e:
                _LOGGER.error(f"An unexpected error occurred during connection: {e}", exc_info=True)
                self.device = None
                return {"error": f"An unexpected error occurred: {e}"}, 500

    async def shell(self, command, timeout_s=15):
        """Execute a shell command on the connected device."""
        if not self.device or not self.device.available:
            _LOGGER.warning("Shell command issued, but device not available. Attempting reconnect...")
            reconnect_result, _ = await self.connect(self.conn_details)
            if reconnect_result.get("status") != "connected":
                 return {"error": "Device is not available and reconnect failed."}, 503

        _LOGGER.info(f"Executing shell command: '{command}'")
        try:
            return await self.device.shell(command, timeout_s=timeout_s), 200
        except (AdbConnectionError, AdbTimeoutError, ConnectionResetError) as e:
            _LOGGER.error(f"ADB Error on shell command '{command}': {e}. Connection lost.")
            self.device = None # Connection is likely dead, reset
            return {"error": str(e)}, 500

    async def tcpip(self, port: int):
        """Enable TCPIP on the device. This MUST be a sync operation over USB."""
        if self.conn_details.get("connection_type") != "USB":
            return {"error": "tcpip can only be enabled on a USB connection"}, 400
        
        serial = self.conn_details.get("serial")
        _LOGGER.info(f"Executing sync tcpip command on port {port} for device {serial}")
        
        try:
            sync_device = AdbDeviceSync(serial=serial, default_transport_timeout_s=9.0)
            await _run_sync(sync_device.connect, rsa_keys=[signer], auth_timeout_s=5.0)
            result = await _run_sync(sync_device.tcpip, port)
            await _run_sync(sync_device.close)
            _LOGGER.info(f"tcpip command result: {result}")
            return {"result": result.strip()}, 200
        except Exception as e:
             _LOGGER.error(f"ADB Error on tcpip command: {e}")
             return {"error": str(e)}, 500

# --- Quart Web Application ---
app = Quart(__name__)

@app.before_serving
async def startup():
    """Initialize the ADB signer and client before starting the server."""
    global signer, adb_client
    signer = await _run_sync(_load_or_generate_keys)
    adb_client = AdbClient()
    _LOGGER.info("Frameo ADB Client Initialized and ready for connection requests.")

# --- API Endpoints ---
@app.route("/health", methods=["POST"])
async def health_check():
    """Check the health of the addon and its connection."""
    if adb_client and adb_client.device and adb_client.device.available:
        return jsonify({"status": "connected", "details": adb_client.conn_details})
    return jsonify({"status": "disconnected"})

@app.route("/devices/usb", methods=["POST"])
async def get_usb_devices():
    """Scan for and return connected USB ADB devices."""
    _LOGGER.info("Request received for /devices/usb")
    try:
        devices = await _run_sync(UsbTransport.find_all_adb_devices)
        serials = [dev.serial_number for dev in devices]
        _LOGGER.info(f"Discovered USB devices: {serials}")
        return jsonify(serials)
    except UsbDeviceNotFoundError:
        _LOGGER.warning("No USB devices found during scan.")
        return jsonify([])
    except Exception as e:
        _LOGGER.error(f"Error finding USB devices: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/connect", methods=["POST"])
async def connect_device():
    """Explicitly connect to a device."""
    conn_details = await request.get_json()
    if not conn_details:
        return jsonify({"error": "Connection details not provided"}), 400
    
    response, status_code = await adb_client.connect(conn_details)
    return jsonify(response), status_code

@app.route("/state", methods=["POST"])
async def get_state():
    """Gets the power and brightness state. Does not require a payload."""
    _LOGGER.info("Request received for /state")
    response, status_code = await adb_client.shell("dumpsys power")
    if status_code >= 400: return jsonify(response), status_code
    
    is_on = "mWakefulness=Awake" in response
    brightness = 0
    for line in response.splitlines():
        if "mScreenBrightnessSetting=" in line:
            try: brightness = int(line.split("=")[1]); break
            except (ValueError, IndexError): pass
    return jsonify({"is_on": is_on, "brightness": brightness})

@app.route("/shell", methods=["POST"])
async def run_shell_command():
    """Runs a shell command. Payload: {"command": "..."}"""
    data = await request.get_json(); command = data.get("command")
    if not command: return jsonify({"error": "Command not provided"}), 400
    response, status_code = await adb_client.shell(command)
    return jsonify({"result": response}), status_code

@app.route("/tcpip", methods=["POST"])
async def enable_tcpip():
    """Enables wireless debugging. Does not require a payload."""
    _LOGGER.info("Request received for /tcpip")
    response, status_code = await adb_client.tcpip(5555)
    return jsonify(response), status_code

@app.route("/ip", methods=["POST"])
async def get_ip_address():
    """Gets the device IP address. Does not require a payload."""
    _LOGGER.info("Request received for /ip")
    response, status_code = await adb_client.shell("ip addr show wlan0")
    if status_code >= 400:
        return jsonify(response), status_code

    ip_info = response
    if ip_info:
        match = re.search(r"inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/", ip_info)
        if match:
            ip_address = match.group(1)
            return jsonify({"ip_address": ip_address})
            
    return jsonify({"error": "Could not find IP address"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)