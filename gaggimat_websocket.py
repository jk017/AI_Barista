import asyncio
import json
import sys
import websockets

# Target the exact channel defined in your AsyncAPI spec
URI = "ws://10.0.0.37:80/ws"

async def send_messages(websocket):
    """
    Simulates sending requests to the firmware.
    You can modify this to send actual requests based on your AsyncAPI spec.
    """
    try:
        while True:
            # Example: Requesting the profiles list every 15 seconds
            request_payload = {
                "tp": "req:profiles:list",
                "rid": "client-init-request"
            }
            await websocket.send(json.dumps(request_payload))
            print(f"🛫 Sent Request: {request_payload['tp']}")
            
            # Keep the loop from spinning out of control
            await asyncio.sleep(15) 
    except asyncio.CancelledError:
        pass

async def receive_messages(websocket):
    """
    Listens for events and responses from the GaggiMate firmware.
    """
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("tp", "unknown")
                
                print(f"\n📥 Received Message Type: [{msg_type}]")
                print(json.dumps(data, indent=2))
                
            except json.JSONDecodeError:
                print(f"⚠️ Received non-JSON raw data: {message}")
    except asyncio.CancelledError:
        pass

async def chat():
    print(f"Connecting to GaggiMate server at {URI}...")
    try:
        async with websockets.connect(URI) as websocket:
            print("✅ Connected to GaggiMate WebSocket successfully!")
            
            # Run receiving and sending concurrently
            await asyncio.gather(
                receive_messages(websocket),
                send_messages(websocket)
            )
    except ConnectionRefusedError:
        print("❌ Could not connect to server. Is the GaggiMate firmware online?")
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"❌ Handshake failed: Server responded with status {e.status_code}.")
        print("   Ensure the path '/ws' is correctly handled by the server proxy.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(chat())
    except KeyboardInterrupt:
        print("\nExiting GaggiMate client...")
        sys.exit(0)