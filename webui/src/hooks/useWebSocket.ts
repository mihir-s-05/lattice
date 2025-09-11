import { useEffect, useRef, useCallback } from 'react'
import { useAppStore } from '@/store'
import type { WebSocketEvent } from '@/types'

interface UseWebSocketOptions {
  runId: string
  baseUrl?: string
  reconnectInterval?: number
  maxReconnectAttempts?: number
}

export function useWebSocket({
  runId,
  baseUrl = (import.meta as any).env?.VITE_WS_BASE ||
    ((typeof window !== 'undefined')
      ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
      : 'ws://localhost:8000'),
  reconnectInterval = 3000,
  maxReconnectAttempts = 5,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const isIntentionalCloseRef = useRef(false)

  const {
    setWebSocket,
    setConnectionState,
    addMessage,
    addEvent,
    addAgentTurn,
    addHuddle,
    updateHuddle,
    addDecision,
    addWebSearch,
    updateGate,
    updateTest,
    updateRun,
    initRunState,
  } = useAppStore()

  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const wsEvent: WebSocketEvent = JSON.parse(event.data)
      
      // Ensure run state exists
      initRunState(runId)
      
      switch (wsEvent.type) {
        case 'run_status': {
          const { data } = wsEvent as any
          updateRun(runId, {
            status: data.status,
            started_at: data.started_at,
            provider: data.provider,
            model: data.model,
          })
          break
        }
        
        case 'router_message': {
          const { data } = wsEvent as any
          addMessage(runId, {
            id: data.message_id,
            role: data.role,
            content: data.content_md,
            timestamp: wsEvent.timestamp,
            annotations: data.annotations,
          })
          break
        }
        
        case 'agent_turn': {
          const { data } = wsEvent as any
          addAgentTurn(runId, {
            id: `${data.agent}-${wsEvent.timestamp}`,
            agent: data.agent,
            phase: data.phase,
            content: data.content_md,
            timestamp: wsEvent.timestamp,
            artifacts_written: data.artifacts_written || [],
            rag_queries: data.rag_queries || [],
            tool_calls: data.tool_calls || [],
          })
          break
        }
        
        case 'huddle_open': {
          const { data } = wsEvent as any
          addHuddle(runId, {
            id: data.huddle_id,
            topic: data.topic,
            attendees: data.attendees,
            status: 'active',
            started_at: wsEvent.timestamp,
            transcript_path: data.transcript_path,
          })
          break
        }
        
        case 'huddle_complete': {
          const { data } = wsEvent as any
          updateHuddle(runId, data.huddle_id, {
            status: 'completed',
            completed_at: wsEvent.timestamp,
            transcript_path: data.transcript_path,
          })
          break
        }
        
        case 'decision_summary_created': {
          const { data } = wsEvent as any
          addDecision(runId, data.summary)
          break
        }
        
        case 'web_search': {
          const { data } = wsEvent as any
          addWebSearch(runId, {
            id: `ws-${wsEvent.timestamp}`,
            ...data,
            timestamp: wsEvent.timestamp,
          })
          break
        }
        
        case 'gate_eval': {
          const { data } = wsEvent as any
          updateGate(runId, data.gate_id, {
            status: data.status,
            checked_conditions: data.checked_conditions,
            evidence: data.evidence,
            last_evaluated: wsEvent.timestamp,
          })
          break
        }
        
        case 'contract_test_result': {
          const { data } = wsEvent as any
          updateTest(runId, data.id, {
            status: data.status,
            metrics: data.metrics,
            evidence: data.evidence,
            last_run: wsEvent.timestamp,
          })
          break
        }
        
        case 'plan_switch':
        case 'provider_switch':
        case 'finalization': {
          addEvent(runId, {
            id: `${wsEvent.type}-${wsEvent.timestamp}`,
            type: wsEvent.type,
            timestamp: wsEvent.timestamp,
            data: wsEvent.data,
          })
          break
        }
      }
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error)
    }
  }, [runId, initRunState, addMessage, addEvent, addAgentTurn, addHuddle, updateHuddle, addDecision, addWebSearch, updateGate, updateTest, updateRun])

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return
    }

    try {
      const wsUrl = `${baseUrl}/runs/${runId}/stream`
      const ws = new WebSocket(wsUrl)
      
      ws.onopen = () => {
        console.log(`WebSocket connected for run ${runId}`)
        setConnectionState(runId, true, false)
        reconnectAttemptsRef.current = 0
        wsRef.current = ws
        setWebSocket(runId, ws)
      }
      
      ws.onmessage = handleMessage
      
      ws.onclose = (event) => {
        console.log(`WebSocket closed for run ${runId}:`, event.code, event.reason)
        setConnectionState(runId, false, false)
        wsRef.current = null
        setWebSocket(runId, null)
        
        // Only attempt reconnection if it wasn't an intentional close
        if (!isIntentionalCloseRef.current && reconnectAttemptsRef.current < maxReconnectAttempts) {
          setConnectionState(runId, false, true)
          reconnectAttemptsRef.current++
          console.log(`Attempting to reconnect (${reconnectAttemptsRef.current}/${maxReconnectAttempts})...`)
          
          reconnectTimeoutRef.current = setTimeout(() => {
            connect()
          }, reconnectInterval)
        }
      }
      
      ws.onerror = (error) => {
        console.error(`WebSocket error for run ${runId}:`, error)
        setConnectionState(runId, false, false)
      }
      
    } catch (error) {
      console.error(`Failed to create WebSocket connection for run ${runId}:`, error)
      setConnectionState(runId, false, false)
    }
  }, [runId, baseUrl, reconnectInterval, maxReconnectAttempts, handleMessage, setConnectionState, setWebSocket])

  const disconnect = useCallback(() => {
    isIntentionalCloseRef.current = true
    
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
      setWebSocket(runId, null)
    }
    
    setConnectionState(runId, false, false)
  }, [runId, setWebSocket, setConnectionState])

  const reconnect = useCallback(() => {
    disconnect()
    isIntentionalCloseRef.current = false
    reconnectAttemptsRef.current = 0
    setTimeout(connect, 100)
  }, [disconnect, connect])

  useEffect(() => {
    if (runId) {
      isIntentionalCloseRef.current = false
      connect()
    }

    return () => {
      disconnect()
    }
  }, [runId, connect, disconnect])

  return {
    reconnect,
    disconnect,
  }
}
