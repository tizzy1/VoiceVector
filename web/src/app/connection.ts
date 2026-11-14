import type { ClientMsg, Role, ServerMsg } from "../protocol";

export function connect(
  role: Role,
  onMessage: (message: ServerMsg) => void,
  onStatus: (status: string) => void,
) {
  let socket: WebSocket;
  let stopped = false;
  let retry = 500;
  let timer: ReturnType<typeof setTimeout>;
  let session = new URLSearchParams(location.search).get("session");
  function open() {
    onStatus("Connecting");
    const query = new URLSearchParams({ role });
    if (session) query.set("session", session);
    socket = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?${query}`,
    );
    socket.onopen = () => { retry = 500; onStatus("Connected"); };
    socket.onmessage = ({ data }) => {
      const message = JSON.parse(data) as ServerMsg;
      if (message.type === "session") session = message.code;
      onMessage(message);
    };
    socket.onclose = ({ code }) => {
      onStatus(code === 1008 ? "Session unavailable" : "Disconnected · retrying");
      if (!stopped && code !== 1008) {
        timer = setTimeout(open, retry);
        retry = Math.min(retry * 2, 8000);
      }
    };
    socket.onerror = () => socket.close();
  }
  open();
  return {
    send(message: ClientMsg): boolean {
      if (socket.readyState !== WebSocket.OPEN) return false;
      socket.send(JSON.stringify(message));
      return true;
    },
    sendBinary(frame: ArrayBuffer): boolean {
      if (socket.readyState !== WebSocket.OPEN) return false;
      socket.send(frame);
      return true;
    },
    close() { stopped = true; clearTimeout(timer); socket.close(); },
  };
}
