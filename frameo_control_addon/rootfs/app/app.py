import os
import asyncio
import logging
from quart import Quart, jsonify, request

from adb_shell.adb_device import AdbDeviceUsb
from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.exceptions import AdbError

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
_LOGGER = logging.getLogger(__name__)

# --- ADB Client Wrapper ---
class AdbClient:
    """A wrapper to manage ADB connections."""
    def __init__(self, loop):
        self._loop = loop
        self.conn_type = os.getenv("CONNECTION_TYPE", "USB")
        _LOGGER.info(f"Initializing ADB client for {self.conn_type} connection.")
        
        if self.conn_type == "USB":
            serial = os.getenv("DEVICE_SERIAL") or None
            _LOGGER.info(f"Using USB with serial: {serial or 'any'}")
            self.device = AdbDeviceUsb(serial=serial, default_transport_timeout_s=9.0)
        else:
            host = os.getenv("DEVICE_HOST")
            port = int(os.getenv("DEVICE_PORT", 5555))
            _LOGGER.info(f"Using Network with host: {host}:{port}")
            self.device = AdbDeviceTcpAsync(host=host, port=port, default_transport_timeout_s=9.0)

    async def _run_sync(self, func, *args):
        """Run a synchronous (blocking) function in an executor."""
        return await self._loop.run_in_executor(None, func, *args)

    async def shell(self, command):
        """Execute a shell command."""
        _LOGGER.info(f"Executing shell command: '{command}'")
        try:
            if self.conn_type == "USB":
                return await self._run_sync(self.device.shell, command)
            return await self.device.shell(command)
        except AdbError as e:
            _LOGGER.error(f"ADB Error on shell command '{command}': {e}")
            return {"error": str(e)}, 500

    async def tcpip(self, port):
        """Enable Wireless ADB on a USB device."""
        if self.conn_type != "USB":
            msg = "tcpip can only be enabled on a USB connection"
            _LOGGER.error(msg)
            return {"error": msg}, 400
        try:
            _LOGGER.info(f"Enabling wireless ADB on port {port}")
            await self._run_sync(self.device.tcpip, port)
            return {"status": "Wireless ADB enabled"}
        except AdbError as e:
            _LOGGER.error(f"ADB Error on tcpip command: {e}")
            return {"error": str(e)}, 500

# --- Quart Web Application ---
app = Quart(__name__)
adb_client = None

@app.before_serving
async def startup():
    """Initialize the ADB client before starting the server."""
    global adb_client
    loop = asyncio.get_running_loop()
    adb_client = AdbClient(loop)
    _LOGGER.info("Frameo ADB Client Initialized and ready.")

@app.route("/health")
async def health_check():
    """Health check endpoint to verify the add-on is running."""
    return jsonify({"status": "ok"})

@app.route("/state", methods=["GET"])
async def get_state():
    """Get the current screen state and brightness."""
    _LOGGER.info("Request received for /state")
    state_result = await adb_client.shell("dumpsys power")
    if isinstance(state_result, tuple):
        return jsonify(state_result[0]), state_result[1]
        
    is_on = "mWakefulness=Awake" in state_result
    brightness = 0
    for line in state_result.splitlines():
        if "mScreenBrightnessSetting=" in line:
            try:
                brightness = int(line.split("=")[1])
                break
            except (ValueError, IndexError):
                pass
    return jsonify({"is_on": is_on, "brightness": brightness})

@app.route("/shell", methods=["POST"])
async def run_shell_command():
    """Run a generic shell command from a POST request."""
    data = await request.get_json()
    command = data.get("command")
    if not command:
        return jsonify({"error": "Command not provided"}), 400

    result = await adb_client.shell(command)
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    
    return jsonify({"result": result})

@app.route("/tcpip", methods=["POST"])
async def enable_tcpip():
    """Endpoint to enable Wireless ADB."""
    _LOGGER.info("Request received for /tcpip")
    result = await adb_client.tcpip(5555)
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
