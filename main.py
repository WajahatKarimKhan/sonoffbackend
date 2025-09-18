import os
import asyncio
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pyewelink import EWeLink

# --- State Management ---
# A simple in-memory dictionary to act as a cache for the sensor data.
cached_data = {
    "temperature": None,
    "humidity": None,
    "last_updated": None,
    "error": None,
}

# --- Background Task for Fetching Data ---
async def fetch_and_cache_data():
    """
    Connects to the eWeLink API, fetches device data, and updates the cache.
    This function now uses the async-native `pyewelink` library.
    """
    email = os.getenv("EWE_EMAIL")
    password = os.getenv("EWE_PASSWORD")
    device_name = os.getenv("EWE_DEVICE_NAME")
    poll_interval = int(os.getenv("POLL_INTERVAL", 60))

    if not all([email, password, device_name]):
        error_msg = "Missing environment variables (EWE_EMAIL, EWE_PASSWORD, EWE_DEVICE_NAME)."
        cached_data["error"] = error_msg
        print(error_msg)
        return  # Stop the task if config is missing

    while True:
        try:
            # The 'pyewelink' library uses an async context manager
            async with EWeLink(email, password) as client:
                devices = await client.get_devices()
                target_device = next((d for d in devices if d.get('name') == device_name), None)

                if not target_device:
                    raise Exception(f"Device named '{device_name}' not found.")

                # The device state is usually included in the get_devices() call
                device_state = target_device.get('params', {})
                
                temp = device_state.get("currentTemperature")
                humidity = device_state.get("currentHumidity")

                if temp is None or humidity is None:
                        raise Exception("Temperature/Humidity data not found in device state.")

                # Update the cache with the new data
                cached_data["temperature"] = temp
                cached_data["humidity"] = humidity
                # CORRECTED: Use datetime for a proper ISO 8601 timestamp
                cached_data["last_updated"] = datetime.now(timezone.utc).isoformat()
                cached_data["error"] = None
                print(f"Successfully fetched data: Temp={temp}°C, Humid={humidity}%")

        except Exception as e:
            error_message = f"An error occurred while fetching data: {e}"
            cached_data["error"] = error_message
            print(error_message)

        await asyncio.sleep(poll_interval)


# --- FastAPI Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # This code runs on startup
    print("Starting background task to fetch Sonoff data...")
    asyncio.create_task(fetch_and_cache_data())
    yield
    # This code would run on shutdown
    print("Shutting down...")

app = FastAPI(lifespan=lifespan)


# --- API Endpoint ---
@app.get("/api/data")
async def get_sonoff_data():
    """
    Returns the latest cached sensor data from the Sonoff THR316.
    """
    if cached_data["error"]:
        # Return a 503 Service Unavailable error if we can't fetch data
        raise HTTPException(status_code=503, detail=cached_data["error"])
    
    if cached_data["last_updated"] is None:
        # If the first poll hasn't completed yet
        raise HTTPException(status_code=503, detail="Data is not available yet. Please try again in a moment.")

    return {
        "temperature": cached_data["temperature"],
        "humidity": cached_data["humidity"],
        "last_updated_timestamp": cached_data["last_updated"],
    }

@app.get("/")
async def root():
    return {"message": "Sonoff Data Fetcher is running. Go to /api/data to see the sensor readings."}
