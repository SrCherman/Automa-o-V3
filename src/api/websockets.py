"""
websockets.py - Endpoint WebSocket para Comunicação em Tempo Real com a UI
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.services.orchestrator import ws_manager

router = APIRouter(tags=["WebSockets"])

@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Manter conexão viva
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
