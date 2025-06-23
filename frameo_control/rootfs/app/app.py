import os
import asyncio
import logging
import re
from quart import Quart, jsonify, request

from adb_shell.adb_device import AdbDeviceUsb
from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.transport.usb_transport import UsbTransport
from adb_shell.exceptions import AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
_LOGGER = logging.getLogger(__name__)

# --- Helper Functions ---
async def _run_sync(func, *args):
    """Run a synchronous (blocking) function in an executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)

async def _get_device_from_request(data):
    """Create a device object from request data."""
    conn_type = data.get("connection_type")
    if conn_type == "USB":
        serial = data.get("serial")
        return AdbDeviceUsb(serial=serial, default_transport_timeout_s=9.0)
    if conn_type == "Network":
        host = data.get("host")
        port = int(data.get("port", 5555))
        return AdbDeviceTcpAsync(host=host, port=port, default_transport_timeout_s=9.0)
    return None

async def _execute_command(data, command, *args):
    """Create, connect, execute a command, and close the connection."""
    device = await _get_device_from_request(data)
    if not device:
        return {"error": "Invalid connection details"}, 400

    try:
        conn_type = data.get("connection_type")
        if conn_type == "USB":
            await _run_sync(device.connect)
            cmd_func = getattr(device, command)
            result = await _run_sync(cmd_func, *args)
        else: # Network
            await device.connect()
            cmd_func = getattr(device, command)
            result = await cmd_func(*args)
        
        return {"result": result}

    except (AdbConnectionError, AdbTimeoutError, UsbDeviceNotFoundError) as e:
        _LOGGER.error(f"ADB Error during '{command}': {e}")
        return {"error": str(e)}, 500
    except Exception as e:
        _LOGGER.error(f"Unexpected error during '{command}': {e}", exc_info=True)
        return {"error": str(e)}, 500
    finally:
        if device:
            if data.get("connection_type") == "USB":
                await _run_sync(device.close)
            else:
                await device.close()

# --- Quart Web Application ---
app = Quart(__name__)

# --- API Endpoints ---
@app.route("/health")
async def health_check():
    """Health check endpoint to verify the add-on is running."""
    return jsonify({"status": "ok"})

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

@app.route("/shell", methods=["POST"])
async def run_shell_command():
    """Run a generic shell command from a POST request."""
    data = await request.get_json()
    command = data.get("command")
    if not command:
        return jsonify({"error": "Command not provided"}), 400
    
    result, status = await _execute_command(data, "shell", command)
    return jsonify(result), status

@app.route("/state", methods=["POST"])
async def get_state():
    """Get the current screen state and brightness for a device."""
    data = await request.get_json()
    
    result, status = await _execute_command(data, "shell", "dumpsys power")

    if status != 200:
        return jsonify(result), status

    state_result = result.get("result")
    if state_result is None:
        return jsonify({"error": "Failed to get device state"}), 500

    is_on = "mWakefulness=Awake" in state_result
    brightness = 0
    for line in state_result.splitlines():
        if "mScreenBrightnessSetting=" in line:
            try: brightness = int(line.split("=")[1]); break
            except (ValueError, IndexError): pass
    return jsonify({"is_on": is_on, "brightness": brightness})

@app.route("/tcpip", methods=["POST"])
async def enable_tcpip():
    """Endpoint to enable Wireless ADB."""
    data = await request.get_json()
    if data.get("connection_type") != "USB":
        return jsonify({"error": "tcpip can only be enabled on a USB connection"}), 400
    
    result, status = await _execute_command(data, "tcpip", 5555)
    return jsonify(result), status

@app.route("/ip", methods=["POST"])
async def get_ip_address():
    """Get the device's IP address."""
    data = await request.get_json()
    
    result, status = await _execute_command(data, "shell", "ip addr show wlan0")
    if status != 200:
        return jsonify(result), status
    
    ip_info = result.get("result")
    if ip_info:
        match = re.search(r"inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/", ip_info)
        if match:
            ip_address = match.group(1)
            return jsonify({"ip_address": ip_address})

    return jsonify({"error": "Could not find IP address"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)