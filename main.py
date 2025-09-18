import os
import asyncio
from fastapi import FastAPI, HTTPException
from ewelink_api import Ewelink

# --- Configuration ---
# It's highly recommended to use environment variables for sensitive data.
# Create a .env file and load them, or set them in your deployment environment.
EWE_EMAIL = os.getenv("EWE_EMAIL", "wkk24084@gmail.com")
EWE_PASSWORD = os.getenv("EWE_PASSWORD", "Aedesign889/-")
EWE_DEVICE_NAME = os.getenv("EWE_DEVICE_NAME", "THR316D") # The exact name in your eWeLink app
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", 60))

# --- In-Memory Data Store ---
# A simple dictionary to cache the latest sensor data.
# In a larger application, you might use Redis or a database.
sensor_data_store = {
    "temperature": None,
    "humidity": None,
    "relay_state": "unknown",
    "last_updated": None,
    "status_message": "Initializing..."
}

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Sonoff Data Fetcher",
    description="An API to fetch data from a Sonoff THR316 via the eWeLink cloud."
)

# --- eWeLink API Logic ---
async def update_sensor_data():
    """
    Connects to the eWeLink API, finds the device, and updates the global data store.
    """
    global sensor_data_store
    try:
        # Initialize the connection to the eWeLink API
        client = Ewelink(EWE_EMAIL, EWE_PASSWORD)
        
        # Find the specific device by its name
        devices = await client.get_devices()
        target_device = next((d for d in devices if d.get('name') == EWE_DEVICE_NAME), None)

        if not target_device:
            print(f"Error: Device named '{EWE_DEVICE_NAME}' not found.")
            sensor_data_store["status_message"] = f"Error: Device not found."
            return

        device_id = target_device.get('deviceid')
        
        # Get the full device status including temp/humidity
        full_status = await client.get_device(device_id)
        params = full_status.get('params', {})
        
        # Update the data store with the latest values
        sensor_data_store.update({
            "temperature": params.get('currentTemperature'),
            "humidity": params.get('currentHumidity'),
            "relay_state": params.get('switch', 'unknown'),
            "last_updated": asyncio.get_event_loop().time(),
            "status_message": "Data successfully updated."
        })
        print(f"Successfully fetched data: {sensor_data_store}")

    except Exception as e:
        print(f"An error occurred while fetching data: {e}")
        sensor_data_store["status_message"] = f"Error: {str(e)}"

async def polling_background_task():
    """
    A background task that runs forever, polling for new data periodically.
    """
    while True:
        await update_sensor_data()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

@app.on_event("startup")
async def app_startup():
    """
    This function is called by FastAPI when the application starts.
    It kicks off our background polling task.
    """
    print("Application startup: Starting background data polling...")
    # Basic validation
    if EWE_EMAIL == "wkk24084@gmail.com" or EWE_PASSWORD == "Aedesign889/-":
        print("WARNING: Using default credentials. Please set EWE_EMAIL and EWE_PASSWORD environment variables.")
        sensor_data_store["status_message"] = "Server is running, but credentials are not set."
    else:
        # Start the background task
        asyncio.create_task(polling_background_task())


# --- API Endpoint ---
@app.get("/api/data")
async def get_sensor_data():
    """
    Returns the most recently fetched sensor data from the in-memory store.
    """
    if sensor_data_store.get("temperature") is None and "Error" in sensor_data_store.get("status_message", ""):
         raise HTTPException(status_code=503, detail=sensor_data_store)

    return sensor_data_store
