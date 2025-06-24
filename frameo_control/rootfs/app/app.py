import os
import asyncio
import logging
import re
from functools import partial
from quart import Quart, jsonify, request
import usb1

from adb_shell.adb_device import AdbDeviceUsb
from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.transport.usb_transport import UsbTransport
from adb_shell.exceptions import AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError
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
    def __init__(self, loop):
        self._loop = loop
        self._lock = asyncio.Lock()
        self.conn_type = os.getenv("CONNECTION_TYPE", "USB")
        
        if self.conn_type == "USB":
            serial = os.getenv("DEVICE_SERIAL") or None
            self.device = AdbDeviceUsb(serial=serial, default_transport_timeout_s=9.0)
        else:
            host = os.getenv("DEVICE_HOST")
            port = int(os.getenv("DEVICE_PORT", 5555))
            self.device = AdbDeviceTcpAsync(host=host, port=port, default_transport_timeout_s=9.0)

    def _auth_callback_sync(self, device_client):
        """Log a message when auth is needed."""
        _LOGGER.info("!!!!!! ACTION REQUIRED !!!!!! Please check your device's screen to 'Allow USB Debugging'.")

    async def connect(self):
        """Establish and maintain the connection to the device."""
        async with self._lock:
            if self.device.available:
                return

            _LOGGER.info("Attempting to connect to device...")
            try:
                if self.conn_type == "USB":
                    await _run_sync(self.device.connect, rsa_keys=[signer], auth_timeout_s=120.0, auth_callback=self._auth_callback_sync)
                else: # Network
                    await self.device.connect(rsa_keys=[signer], auth_timeout_s=20.0)
                _LOGGER.info("Successfully connected to device.")
            except (AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError, usb1.USBError) as e:
                _LOGGER.error(f"Failed to connect to device: {e}")
                try:
                    if self.conn_type == "USB":
                        await _run_sync(self.device.close)
                    else:
                        await self.device.close()
                except Exception as close_e:
                    _LOGGER.error(f"Error during connection-failure cleanup: {close_e}")


    async def shell(self, command):
        """Execute a shell command, ensuring connection first."""
        async with self._lock:
            if not self.device.available:
                await self.connect()
            
            if not self.device.available:
                return {"error": "Device is not available after connection attempt."}, 500

            _LOGGER.info(f"Executing shell command: '{command}'")
            try:
                if self.conn_type == "USB":
                    return await _run_sync(self.device.shell, command), 200
                return await self.device.shell(command), 200
            except (AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError, usb1.USBError) as e:
                _LOGGER.error(f"ADB Error on shell command '{command}': {e}")
                return {"error": str(e)}, 500
    
    async def tcpip(self, port):
        """Enable tcpip on a USB device."""
        async with self._lock:
            if not self.device.available:
                await self.connect()

            if not self.device.available:
                return {"error": "Device is not available after connection attempt."}, 500
            
            _LOGGER.info(f"Executing tcpip command on port {port}")
            try:
                if self.conn_type == "USB":
                    return await self._run_sync(self.device.tcpip, port), 200
                else:
                    return {"error": "tcpip can only be enabled on a USB connection"}, 400
            except (AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError, usb1.USBError) as e:
                _LOGGER.error(f"ADB Error on tcpip command: {e}")
                return {"error": str(e)}, 500


# --- Quart Web Application ---
app = Quart(__name__)

@app.before_serving
async def startup():
    """Initialize the ADB signer and client before starting the server."""
    global signer, adb_client
    signer = await _run_sync(_load_or_generate_keys)
    loop = asyncio.get_running_loop()
    adb_client = AdbClient(loop)
    _LOGGER.info("Frameo ADB Client Initialized.")

# --- API Endpoints ---
@app.route("/health")
async def health_check():
    if adb_client:
        return jsonify({"status": "ok", "device_available": adb_client.device.available})
    return jsonify({"status": "initializing"})

@app.route("/devices/usb")
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

@app.route("/state", methods=["GET"])
async def get_state():
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
    data = await request.get_json(); command = data.get("command")
    if not command: return jsonify({"error": "Command not provided"}), 400
    response, status_code = await adb_client.shell(command)
    return jsonify({"result": response}), status_code

@app.route("/tcpip", methods=["POST"])
async def enable_tcpip():
    _LOGGER.info("Request received for /tcpip")
    response, status_code = await adb_client.tcpip(5555)
    return jsonify(response), status_code

@app.route("/ip", methods=["GET"])
async def get_ip_address():
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