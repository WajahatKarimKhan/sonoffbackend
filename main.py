import os
import secrets
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import requests
import asyncio
from fastapi import WebSocket, WebSocketDisconnect

# --- Configuration ---
# It's best practice to load these from environment variables
client_id = os.getenv('EWELINK_APP_ID', 'V7pwdsy9Cy66SxXY9gwrxPuiQW4tu5w2')
client_secret = os.getenv('EWELINK_APP_SECRET', 'MbzyC3kUIdgeQiXTgx8aahNqzquJ8Dfs')

# Your live backend's callback URL
redirect_uri = 'https://aedesign-sonoff-backend.onrender.com/callback'
# Your live frontend's URL
react_app_url = 'https://aedesign-sonoffs-app.onrender.com'

# --- NEW CHANGE: Forcing all API endpoints to the Asia region ---
asia_api_base = 'https://as-apia.coolkit.cc'
authorization_base_url = f'{asia_api_base}/oauth/authorize'
token_url = f'{asia_api_base}/oauth/token'

app = FastAPI()

# --- Middleware ---
# CORS Middleware to allow requests from your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[react_app_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session Middleware to handle user sessions
# A secret key is required to sign the session cookie
app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(16))


# --- WebSocket Management ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

    async def send_json(self, message: dict, user_id: str):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_json(message)

manager = ConnectionManager()


# --- API Routes ---
@app.get("/login")
def login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session['oauth_state'] = state
    authorization_url = (
        f"{authorization_base_url}?response_type=code&client_id={client_id}"
        f"&redirect_uri={redirect_uri}&state={state}&scope=user:read"
    )
    return RedirectResponse(url=authorization_url)


@app.get("/callback")
def callback(request: Request, code: str, state: str):
    stored_state = request.session.get('oauth_state')
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=403, detail="State mismatch.")

    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': client_id,
        'client_secret': client_secret,
    }
    
    try:
        response = requests.post(token_url, json=data)
        response.raise_for_status()
        token_data = response.json()

        # Store the token and the user's region in the session
        request.session['token'] = token_data
        
        # --- NEW CHANGE BASED ON DOCUMENTATION ---
        # Extract and store the user's region from the token response
        request.session['region'] = token_data.get('region', 'cn') # Default to 'cn' if not present
        # --- END NEW CHANGE ---

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch token: {e}")

    # Redirect user back to the frontend application
    return RedirectResponse(url=react_app_url)


@app.get("/api/status")
def get_status(request: Request):
    return {"authenticated": 'token' in request.session}


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=react_app_url)


@app.get("/api/get-data")
async def get_data_trigger(request: Request):
    if 'token' not in request.session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = request.session.get('user_id')
    if not user_id:
        raise HTTPException(status_code=403, detail="No active WebSocket session for this user.")

    token = request.session['token']['access_token']
    
    # --- NEW CHANGE BASED ON DOCUMENTATION ---
    # Dynamically build the API URL using the stored region
    region = request.session.get('region', 'cn') # Default to 'cn'
    api_url = f"https://{region}-api.coolkit.cc:8080/v2/user/device"
    # --- END NEW CHANGE ---
    
    headers = {'Authorization': f'Bearer {token}'}

    try:
        print(f"Fetching data from: {api_url}") # For debugging
        response = requests.get(api_url, headers=headers)
        
        print(f"eWeLink API Response Status: {response.status_code}") # For debugging
        print(f"eWeLink API Response Body: {response.text}") # For debugging
        
        response.raise_for_status()
        data = response.json()
        
        # Push data to the specific user's WebSocket
        await manager.send_json(data, user_id)
        return {"status": "Data fetch triggered and sent via WebSocket"}
        
    except requests.exceptions.RequestException as e:
        error_details = {"error": "Failed to fetch data from eWeLink API", "details": str(e)}
        await manager.send_json(error_details, user_id)
        raise HTTPException(status_code=502, detail=error_details)
    except Exception as e:
        error_details = {"error": "An unexpected error occurred", "details": str(e)}
        await manager.send_json(error_details, user_id)
        raise HTTPException(status_code=500, detail=error_details)
        

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # This is a simplified user identification for the WebSocket.
    # In a real app, you would have a more robust way to associate a user with a WebSocket.
    try:
        session = websocket.scope['session']
        if 'token' not in session:
            await websocket.close(code=1008)
            return
            
        # A simple unique identifier for the user's session
        user_id = session.get('user_id')
        if not user_id:
            user_id = secrets.token_hex(8)
            session['user_id'] = user_id

        await manager.connect(websocket, user_id)
        try:
            while True:
                # Keep the connection alive
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(user_id)
            print(f"WebSocket disconnected for user {user_id}")
    except KeyError:
        # This can happen if the session middleware isn't working correctly
        print("WebSocket connection failed: Session not found in scope.")
        await websocket.close(code=1011)

