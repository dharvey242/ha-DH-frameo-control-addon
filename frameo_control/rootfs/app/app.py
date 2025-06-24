import os
import asyncio
import logging
import re
from functools import partial
from quart import Quart, jsonify, request
import usb1

from adb_shell.adb_device import AdbDevice as AdbDeviceSync, AdbDeviceUsb
from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.transport.usb_transport import UsbTransport
from adb_shell.exceptions import AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
_LOGGER = logging.getLogger(__name__)

# --- Global Signer and Client ---
signer = None
adb_client = None
is_usb = False
connection_details_store = {}

# --- Helper Functions ---
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

def _auth_callback_sync(device_client):
    """Log a message when auth is needed. This is only for sync (USB) connections."""
    _LOGGER.info("!!!!!! ACTION REQUIRED !!!!!! Please check your device's screen to 'Allow USB Debugging'.")

async def _do_connect(conn_details):
    global adb_client, is_usb
    conn_type = conn_details.get("connection_type", "USB").upper()

    try:
        if conn_type == "USB":
            is_usb = True
            serial = conn_details.get("serial")
            adb_client = AdbDeviceUsb(serial=serial, default_transport_timeout_s=9.0)
            await _run_sync(adb_client.connect, rsa_keys=[signer], auth_timeout_s=120.0, auth_callback=_auth_callback_sync)
        else: # NETWORK
            is_usb = False
            host = conn_details.get("host")
            port = int(conn_details.get("port", 5555))
            adb_client = AdbDeviceTcpAsync(host=host, port=port, default_transport_timeout_s=9.0)
            await adb_client.connect(rsa_keys=[signer], auth_timeout_s=20.0)
        
        _LOGGER.info(f"Successfully connected to device: {conn_details.get('serial') or conn_details.get('host')}")
        return {"status": "connected"}, 200
    except (AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError, usb1.USBError, ConnectionResetError) as e:
        _LOGGER.error(f"Failed to connect to device: {e}")
        adb_client = None
        return {"error": f"Connection failed: {e}"}, 500
    except Exception as e:
        _LOGGER.error(f"An unexpected error occurred during connection: {e}", exc_info=True)
        adb_client = None
        return {"error": f"An unexpected error occurred: {e}"}, 500

async def _ensure_connection():
    """Check if the client is available, and if not, try to reconnect."""
    global adb_client
    if adb_client and adb_client.available:
        return True

    _LOGGER.warning("Connection lost or not established. Attempting to reconnect...")
    if not connection_details_store:
        _LOGGER.error("Cannot reconnect: No connection details have been stored.")
        return False
    
    response, status_code = await _do_connect(connection_details_store)
    if status_code == 200:
        return True
    
    _LOGGER.error(f"Reconnect failed: {response}")
    return False

# --- Quart Web Application ---
app = Quart(__name__)

@app.before_serving
async def startup():
    global signer
    signer = await _run_sync(_load_or_generate_keys)
    _LOGGER.info("Frameo ADB Server Initialized.")

# --- API Endpoints ---

@app.route("/health", methods=["GET"])
async def health_check():
    """Check the health of the addon and its connection."""
    if adb_client and adb_client.available:
        return jsonify({"status": "connected", "details": connection_details_store})
    return jsonify({"status": "disconnected"})

@app.route("/devices/usb", methods=["GET"])
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
async def connect_device_endpoint():
    """API endpoint to explicitly connect to a device."""
    global connection_details_store
    connection_details_store = await request.get_json()
    if not connection_details_store:
        return jsonify({"error": "Connection details not provided"}), 400
    
    response, status_code = await _do_connect(connection_details_store)
    return jsonify(response), status_code

async def _shell_command_with_reconnect(command):
    """Wrapper for shell commands that ensures connection first."""
    global adb_client
    
    if not await _ensure_connection():
        return {"error": "Device is not connected and reconnect failed."}, 503

    _LOGGER.info(f"Executing shell command: '{command}'")
    try:
        if is_usb:
            response = await _run_sync(adb_client.shell, command)
        else:
            response = await adb_client.shell(command)
        return response, 200
    except (AdbConnectionError, AdbTimeoutError, ConnectionResetError, usb1.USBError) as e:
        _LOGGER.error(f"ADB Error on shell command '{command}': {e}. Marking connection as lost.")
        adb_client = None
        return {"error": str(e)}, 500

@app.route("/state", methods=["POST"])
async def get_state():
    response, status_code = await _shell_command_with_reconnect("dumpsys power")
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
    response, status_code = await _shell_command_with_reconnect(command)
    return jsonify({"result": response}), status_code

@app.route("/ip", methods=["POST"])
async def get_ip_address():
    response, status_code = await _shell_command_with_reconnect("ip addr show wlan0")
    if status_code >= 400: return jsonify(response), status_code
    if response:
        match = re.search(r"inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/", response)
        if match: return jsonify({"ip_address": match.group(1)})
    return jsonify({"error": "Could not find IP address"}), 404

@app.route("/tcpip", methods=["POST"])
async def enable_tcpip():
    """Enables wireless debugging with automatic reconnection."""
    global adb_client
    
    _LOGGER.info("Request received for /tcpip")

    if not await _ensure_connection():
        return jsonify({"error": "Device is not connected and reconnect failed."}), 503

    if not is_usb:
        return jsonify({"error": "tcpip can only be enabled on a USB connection"}), 400

    try:
        # tcpip is a synchronous command, so it must be run in an executor.
        result = await _run_sync(adb_client.tcpip, 5555)
        return jsonify({"result": result.strip()}), 200
    except (AdbConnectionError, AdbTimeoutError, ConnectionResetError, usb1.USBError) as e:
        _LOGGER.error(f"ADB Error on tcpip command: {e}. Marking connection as lost.")
        adb_client = None # Mark connection as dead
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)