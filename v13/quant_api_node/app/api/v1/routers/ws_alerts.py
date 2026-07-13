"""WebSocket 路由 — /ws/alerts 双向流式告警。

发布:  Redis pub/sub 通道接收 alert 事件 → 转发给所有连接的 client
客户端可发送 ping (JSON: {"type": "ping"}) 触发 pong 回应
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from v13.quant_api_node.app.core.dependencies import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/alerts")
async def websocket_alerts(ws: WebSocket) -> None:
    """实时告警推送。

    客户端期望::

        ws = new WebSocket("ws://localhost/ws/alerts")
        ws.onmessage = (e) => append(e.data)   // JSON: {type, ticker, severity, ...}
    """
    await ws.accept()
    redis = get_redis()
    pubsub = redis.subscribe_alerts()
    if pubsub is None:
        # 降级：定期发送空闲心跳
        try:
            while True:
                await asyncio.sleep(15)
                await ws.send_json({"type": "heartbeat", "ts": asyncio.get_event_loop().time()})
        except WebSocketDisconnect:
            return
        except Exception:
            return

    # 把同步 pubsub 拉取卸载到后台线程
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=128)

    def pump() -> None:
        try:
            for msg in pubsub.listen():
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                loop.call_soon_threadsafe(queue.put_nowait, data)
        except Exception as e:
            logger.warning("pubsub 拉取循环异常: %s", e)

    pump_task = loop.run_in_executor(None, pump)
    try:
        await ws.send_json({"type": "ready", "ts": asyncio.get_event_loop().time()})
        while True:
            await asyncio.sleep(0.05)
            # 推送队列
            try:
                while not queue.empty():
                    raw = queue.get_nowait()
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        await ws.send_text(raw if isinstance(raw, str) else json.dumps(raw))
                    except Exception:
                        return
            except Exception:
                pass
            # 接收客户端 ping
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=0.01)
                obj = json.loads(msg)
                if obj.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                raise
            except Exception:
                pass
    except WebSocketDisconnect:
        logger.info("WebSocket 客户端断开")
    finally:
        try:
            pump_task.cancel()
        except Exception:
            pass
        try:
            pubsub.close()
        except Exception:
            pass
