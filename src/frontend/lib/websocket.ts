/**
 * WebSocket client for real-time status updates from the backend.
 */

export class StatusWebSocket {
  private ws: WebSocket | null = null;
  private onStatusUpdate: (message: string) => void;
  private onConnect?: () => void;

  constructor(
    onStatusUpdate: (message: string) => void,
    onConnect?: () => void,
  ) {
    this.onStatusUpdate = onStatusUpdate;
    this.onConnect = onConnect;
  }

  connect(sessionId: string): void {
    const wsUrl =
      process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
    this.ws = new WebSocket(`${wsUrl}/${sessionId}`);

    this.ws.onopen = () => {
      this.onConnect?.();
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "status") {
        this.onStatusUpdate(data.message);
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
    };
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
  }
}
