import { useEffect, useRef, useState } from "react";
import { TransactionEvent, wsUrl } from "../lib/api";

export function useLiveTransactions() {
  const [events, setEvents] = useState<TransactionEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const retryRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: number | undefined;

    const connect = () => {
      ws = new WebSocket(wsUrl("/ws/transactions"));
      ws.onopen = () => {
        setConnected(true);
        retryRef.current = 0;
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          const delay = Math.min(8000, 500 * 2 ** retryRef.current++);
          timer = window.setTimeout(connect, delay);
        }
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "snapshot") {
            setEvents(msg.events || []);
          } else if (msg.type === "event" && msg.event) {
            setEvents((prev) => [...prev.slice(-199), msg.event]);
          }
        } catch {
          /* ignore */
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { events, connected };
}
