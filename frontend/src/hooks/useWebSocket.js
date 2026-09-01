import { useEffect, useRef, useCallback } from 'react';
import { createWebSocket } from '../api';

/**
 * Enhanced WebSocket hook with automatic reconnection
 *
 * @param {string} endpoint - WebSocket endpoint (e.g., 'logs', 'metrics')
 * @param {function} onMessage - Message handler
 * @param {object} options - Options { reconnectInterval, maxReconnectAttempts }
 */
export function useWebSocket(endpoint, onMessage, options = {}) {
  const {
    reconnectInterval = 3000,
    maxReconnectAttempts = 10,
    onConnect = null,
    onDisconnect = null,
    onError = null,
  } = options;

  const wsRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef(null);

  const connect = useCallback(() => {
    try {
      const ws = createWebSocket(endpoint);

      ws.onopen = () => {
        console.log(`WebSocket connected to ${endpoint}`);
        reconnectAttemptsRef.current = 0;
        if (onConnect) onConnect();
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessage(data);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
        }
      };

      ws.onerror = (error) => {
        console.error(`WebSocket error on ${endpoint}:`, error);
        if (onError) onError(error);
      };

      ws.onclose = () => {
        console.log(`WebSocket disconnected from ${endpoint}`);
        if (onDisconnect) onDisconnect();

        // Attempt to reconnect
        if (reconnectAttemptsRef.current < maxReconnectAttempts) {
          reconnectAttemptsRef.current += 1;
          console.log(
            `Attempting to reconnect (${reconnectAttemptsRef.current}/${maxReconnectAttempts})...`
          );

          reconnectTimerRef.current = setTimeout(() => {
            connect();
          }, reconnectInterval);
        } else {
          console.error(`Max reconnection attempts reached for ${endpoint}`);
        }
      };

      wsRef.current = ws;
    } catch (error) {
      console.error('Failed to create WebSocket:', error);
    }
  }, [endpoint, onMessage, reconnectInterval, maxReconnectAttempts, onConnect, onDisconnect, onError]);

  useEffect(() => {
    connect();

    return () => {
      // Cleanup
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    if (wsRef.current) {
      wsRef.current.close();
    }
    reconnectAttemptsRef.current = maxReconnectAttempts; // Prevent reconnection
  }, [maxReconnectAttempts]);

  return { disconnect };
}
