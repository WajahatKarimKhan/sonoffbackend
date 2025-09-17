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
client_id = os.getenv('EWELINK_APP_ID', 'V7pwdsy9Cy66SxXY9gwrxPuiQW4tu5w2')
client_secret = os.getenv('EWELINK_APP_SECRET', 'MbzyC3kUIdgeQiXTgx8aahNqzquJ8Dfs')

redirect_uri = 'https://aedesign-sonoff-backend.onrender.com/callback'
react_app_url = 'https://aedesign-sonoffs-app.onrender.com'

# --- CORRECTED CHANGE: Using global endpoints for authentication ---
# These are the documented, official URLs for the OAuth process
authorization_base_url = 'https://app-api.coolkit.cn/oauth/authorize'
token_url = 'https://app-api.coolkit.cn/oauth/token'
# --- END CORRECTION ---


app = FastAPI()

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[react_app_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
        request.session['token'] = token_data
        # We still save the region for later use
        request.session['region'] = token_data.get('region', 'as') # Default to 'as'
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch token: {e}")

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
    
    # We use the correct, region-specific URL for data fetching as per the docs
    region = request.session.get('region', 'as') # Default to 'as' if not found
    api_url = f"https://{region}-apia.coolkit.cc:8080/v2/user/device"
    
    headers = {'Authorization': f'Bearer {token}'}

    try:
        print(f"Fetching data from: {api_url}")
        response = requests.get(api_url, headers=headers)
        print(f"eWeLink API Response Status: {response.status_code}")
        print(f"eWeLink API Response Body: {response.text}")
        response.raise_for_status()
        data = response.json()
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
    try:
        session = websocket.scope['session']
        if 'token' not in session:
            await websocket.close(code=1008)
            return
            
        user_id = session.get('user_id')
        if not user_id:
            user_id = secrets.token_hex(8)
            session['user_id'] = user_id

        await manager.connect(websocket, user_id)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(user_id)
            print(f"WebSocket disconnected for user {user_id}")
    except KeyError:
        print("WebSocket connection failed: Session not found in scope.")
        await websocket.close(code=1011)

