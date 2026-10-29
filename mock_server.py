"""
Standalone WebSocket Event Receiver Server.
Listens for student appearance events dispatched by the Face Recognition system.
"""
import asyncio
import json
import datetime
import websockets
import argparse

async def handle_client(websocket):
    client_ip = websocket.remote_address
    print(f"\n[+] Camera Client Connected: {client_ip}")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print("=" * 70)
                print(f"[{now_str}] EVENT RECEIVED: {data.get('event', 'UNKNOWN')}")
                print(f"  • Student ID : {data.get('student_id')}")
                print(f"  • Name       : {data.get('student_name')}")
                print(f"  • Camera ID  : {data.get('camera_id')}")
                print(f"  • Confidence : {float(data.get('confidence', 0)) * 100:.1f}%")
                print(f"  • Latency    : {data.get('inference_latency_ms', 0):.1f} ms")
                print(f"  • Timestamp  : {data.get('timestamp')}")
                print(f"  • Box (xywh) : {data.get('bbox')}")
                meta = data.get("metadata")
                if meta and isinstance(meta, dict) and len(meta) > 1:
                    print(f"  • Metadata   : {meta}")
                print("=" * 70)
                
                # Acknowledge receipt
                await websocket.send(json.dumps({"status": "ACK", "student_id": data.get("student_id")}))
            except json.JSONDecodeError:
                print(f"[!] Received non-JSON payload: {message}")
    except websockets.exceptions.ConnectionClosed:
        print(f"[-] Camera Client Disconnected: {client_ip}")

async def main(host: str, port: int):
    print(f"============================================================")
    print(f" Real-Time Face Recognition WebSocket Receiver Server")
    print(f" Listening on ws://{host}:{port}/face-events")
    print(f" Press Ctrl+C to terminate server")
    print(f"============================================================")
    async with websockets.serve(handle_client, host, port):
        await asyncio.Future() # run forever

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebSocket Event Receiver Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[*] WebSocket Server stopped.")
