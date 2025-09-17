import os
import json
import requests
import secrets
from typing import Dict
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

# --- Configuration ---
client_id = os.getenv('EWELINK_APP_ID', 'V7pwdsy9Cy66SxXY9gwrxPuiQW4tu5w2')
client_secret = os.getenv('EWELINK_APP_SECRET', 'MbzyC3kUIdgeQiXTgx8aahNqzquJ8Dfs')
# --- MODIFIED LINE ---
redirect_uri = 'http://localhost:8000/callback' # Changed port to 8000
# --- END MODIFICATION ---
react_app_url = 'http://localhost:3000'
authorization_base_url = 'https://app-api.coolkit.cn/oauth/authorize'
token_url = 'https://app-api.coolkit.cn/oauth/token'
api_base_url = 'https://app-api.coolkit.cn/v2'


# --- WebSocket Connection Manager ---
class ConnectionManager:
    """Manages active WebSocket connections."""
    def __init__(self):
        # Maps a user's access token to their active WebSocket
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, token: str):
        """Accepts a new connection."""
        await websocket.accept()
        self.active_connections[token] = websocket

    def disconnect(self, token: str):
        """Removes a connection."""
        if token in self.active_connections:
            del self.active_connections[token]

    async def send_personal_message(self, message: str, token: str):
        """Sends a message to a specific user's WebSocket."""
        if token in self.active_connections:
            await self.active_connections[token].send_text(message)

    async def send_json_message(self, data: dict, token: str):
        """Sends a JSON payload to a specific user."""
        if token in self.active_connections:
            await self.active_connections[token].send_json(data)

manager = ConnectionManager()


# --- FastAPI Application Setup ---
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(16))
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- HTTP Routes for OAuth ---
# These routes handle the initial authentication and session creation.

@app.get('/')
async def index():
    return {"message": "eWeLink WebSocket Backend is running!"}

@app.get('/login')
async def login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session['oauth_state'] = state
    authorization_url = (
        f"{authorization_base_url}?response_type=code&client_id={client_id}&"
        f"redirect_uri={redirect_uri}&state={state}&scope=user:read"
    )
    return RedirectResponse(url=authorization_url)

@app.get('/callback')
async def callback(request: Request, code: str = None, state: str = None):
    if not state or state != request.session.get('oauth_state'):
        raise HTTPException(status_code=400, detail="Invalid state parameter.")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided.")

    token_payload = {
        'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
        'client_id': client_id, 'client_secret': client_secret
    }
    try:
        response = requests.post(token_url, json=token_payload)
        response.raise_for_status()
        token_data = response.json()
        request.session['access_token'] = token_data.get('accessToken')
        request.session['refresh_token'] = token_data.get('refreshToken')
        return RedirectResponse(url=react_app_url)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve access token: {e}")

@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url=react_app_url)

# --- API Routes interacting with WebSockets ---

@app.get('/api/status')
async def get_status(request: Request):
    """Checks if the user has a valid session."""
    if request.session.get('access_token'):
        return {"authenticated": True}
    return {"authenticated": False}


@app.get('/api/get-data')
async def trigger_get_data(request: Request):
    """
    Fetches data from eWeLink and pushes it to the client via WebSocket.
    """
    access_token = request.session.get('access_token')
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        api_url = f"{api_base_url}/user/device"
        print(f"--- Making request to eWeLink API: {api_url} ---")
        response = requests.get(api_url, headers=headers)
        
        # --- ADDED FOR DEBUGGING ---
        # Print the status code and the raw response text to the terminal
        print(f"eWeLink API Response Status Code: {response.status_code}")
        print(f"eWeLink API Raw Response: {response.text}")
        # --- END DEBUGGING ---
        
        response.raise_for_status()
        data = response.json()
        
        # Instead of returning data, send it through the WebSocket
        await manager.send_json_message(data, access_token)
        
        return {"message": "Data sent over WebSocket."}
    except requests.exceptions.RequestException as e:
        error_details = {'error': 'Failed to fetch data from eWeLink API', 'details': str(e)}
        print(f"--- ERROR: {error_details} ---") # Also print the error
        await manager.send_json_message(error_details, access_token)
        raise HTTPException(status_code=500, detail=error_details)


# --- WebSocket Endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handles WebSocket connections. A client connects here after authenticating
    via the standard HTTP OAuth flow. The access token from the session is used
    to identify the connection.
    """
    # CORRECTED LINE: Access session data through `websocket.scope['session']`
    access_token = websocket.scope['session'].get('access_token')
    if not access_token:
        # If there's no token in the session, close the connection.
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, access_token)
    print(f"WebSocket connected for token: ...{access_token[-5:]}")
    try:
        while True:
            # Keep the connection alive, waiting for potential client messages.
            # For this app, we are only pushing server->client.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(access_token)
        print(f"WebSocket disconnected for token: ...{access_token[-5:]}")

# To run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

