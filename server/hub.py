"""Independent, ordered output queues for each browser."""
import asyncio
import logging
from collections import deque

from fastapi import WebSocket
from pydantic import BaseModel

from server.contracts import Event, EventMsg, Role
from server.feed import feed_text

logger = logging.getLogger(__name__)


class Client:
    def __init__(self, socket: WebSocket, role: Role):
        self.socket = socket
        self.role = role
        self.queue: deque[dict] = deque()
        self.available = asyncio.Event()
        self.writer: asyncio.Task | None = None

    def enqueue(self, message: dict) -> None:
        if message["type"] == "state":
            self.queue = deque(m for m in self.queue if m["type"] != "state")
        self.queue.append(message)
        self.available.set()

    async def write(self) -> None:
        try:
            while True:
                await self.available.wait()
                while self.queue:
                    await asyncio.wait_for(self.socket.send_json(self.queue.popleft()), 10)
                self.available.clear()
        except (TimeoutError, RuntimeError, OSError):
            logger.warning("Closing stalled or disconnected viewer")
            await self.socket.close()


class Hub:
    def __init__(self):
        self.clients: dict[WebSocket, Client] = {}
        self.tasks: set[asyncio.Task] = set()

    def spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Background operation failed", exc_info=task.exception())

    async def connect(self, socket: WebSocket, role: Role) -> Client:
        await socket.accept()
        client = Client(socket, role)
        self.clients[socket] = client
        client.writer = self.spawn(client.write())
        return client

    async def disconnect(self, socket: WebSocket) -> None:
        client = self.clients.pop(socket, None)
        if client and client.writer:
            client.writer.cancel()
            await asyncio.gather(client.writer, return_exceptions=True)

    def counts(self) -> dict[str, int]:
        return {role: sum(c.role == role for c in self.clients.values())
                for role in ("radar", "viz")}

    async def send(self, msg: BaseModel | dict, roles: set[Role] | None = None) -> None:
        data = msg.model_dump(mode="json") if isinstance(msg, BaseModel) else msg
        for client in tuple(self.clients.values()):
            if roles is None or client.role in roles:
                client.enqueue(data)

    def on_event(self, event: Event) -> None:
        self.spawn(self.send(EventMsg(event=event, feed_text=feed_text(event))))

    async def close(self) -> None:
        for socket in tuple(self.clients):
            await self.disconnect(socket)
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
