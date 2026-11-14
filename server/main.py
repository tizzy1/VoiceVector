"""Run with: python -m uvicorn server.main:app --host 0.0.0.0 --port 8000."""
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError

from server.contracts import ControlMsg, PttMsg, TypedMsg
from server.hub import Hub
from server.session import ROOT, Session

load_dotenv(ROOT / ".env")
logger = logging.getLogger(__name__)
CLIENT_MESSAGE = TypeAdapter(ControlMsg | PttMsg | TypedMsg)


def lan_url() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("192.0.2.1", 80))
            return f"http://{connection.getsockname()[0]}:8000"
    except OSError:
        return "http://localhost:8000"


def create_app(factories: dict | None = None, runs_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = Hub()
        app.state.session = Session(app.state.hub, factories, runs_dir)
        await app.state.session.start()
        yield
        await app.state.session.stop()
        await app.state.hub.close()

    app = FastAPI(title="SweatboxAI", lifespan=lifespan)

    @app.get("/api/health")
    async def health():
        session = app.state.session
        return {"ready": session.ready, "simulation_ready": session.simulation_ready,
                "missing_modules": session.missing}

    @app.get("/api/session")
    async def session_info():
        return app.state.session.message(lan_url())

    @app.get("/api/data/{name}")
    async def data(name: str):
        allowed = {"egkk", "perf", "telephony", "traffic", "scenario_demo"}
        if name not in allowed:
            raise HTTPException(404, "Unknown data file")
        return FileResponse(ROOT / "data" / f"{name}.json")

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        role = websocket.query_params.get("role", "radar")
        session, hub = app.state.session, app.state.hub
        code = websocket.query_params.get("session")
        if role not in ("radar", "viz") or (code and code != session.code):
            await websocket.close(code=1008)
            return
        client = await hub.connect(websocket, role)
        await hub.send(session.message(lan_url()))
        client.enqueue(session.state().model_dump(mode="json"))
        if session.report:
            client.enqueue(session.report.model_dump(mode="json"))
        if not session.ready:
            await session.notice("Radio unavailable. Waiting for the voice pipeline.")
        try:
            while True:
                frame = await websocket.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                try:
                    if frame.get("bytes") is not None:
                        if role != "radar" or session.controller is not websocket:
                            raise ValueError("Audio requires active radar push-to-talk")
                        if len(frame["bytes"]) > 64000:
                            raise ValueError("Audio frame too large")
                        await session.voice.on_audio_in(frame["bytes"])
                        continue
                    text = frame.get("text", "")
                    if len(text) > 16000:
                        raise ValueError("Message too large")
                    message = CLIENT_MESSAGE.validate_json(text)
                    if isinstance(message, ControlMsg):
                        if message.action == "import_live":
                            session.start_operation(session.handle_control(message))
                            continue
                        await session.handle_control(message)
                        await hub.send(session.message(lan_url()))
                        continue
                    if role != "radar":
                        raise ValueError("Only the radar can transmit")
                    if not session.ready:
                        raise ValueError("Radio unavailable. Waiting for the voice pipeline.")
                    if isinstance(message, TypedMsg):
                        if not message.text.strip() or len(message.text) > 2000:
                            raise ValueError("Enter a clearance of 1–2000 characters")
                        session.start_operation(session.voice.on_typed(message.text.strip()))
                    elif message.state == "down":
                        if session.controller is not None:
                            raise ValueError("Controller frequency already keyed")
                        session.controller = websocket
                        try:
                            await session.voice.on_ptt("down")
                        except Exception:
                            session.controller = None
                            raise
                    elif session.controller is websocket:
                        session.controller = None
                        session.start_operation(session.voice.on_ptt("up"))
                except (ValidationError, ValueError, TypeError) as error:
                    await session.notice(str(error))
                except Exception:
                    logger.exception("Transmission failed")
                    await session.notice("Transmission failed. Try again or use typed input.")
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if session.controller is websocket:
                session.controller = None
                session.start_operation(session.voice.on_ptt("up"))
            await hub.disconnect(websocket)
            await hub.send(session.message(lan_url()))

    dist = ROOT / "web" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    @app.get("/viz")
    @app.get("/report")
    async def index():
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "Build the web app with npm run build in web/")
        return FileResponse(dist / "index.html")

    return app


app = create_app()
