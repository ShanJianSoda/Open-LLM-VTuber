"""
MaiBot 桥接模块：将 open-llm-vtuber 的用户输入以「不携带历史」的 message_data 格式
发给 MaiBot 的 message_process，并接收 MaiBot 的回复。

格式与实现参考：开发记录/VTuber与MaiBot双向接入_格式与实现.md
"""

import asyncio
import json
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger

# 可选依赖：异步 WebSocket 客户端
try:
    import websockets
except ImportError:
    websockets = None

PLATFORM = "openllm_vtuber"
USER_ID_PREFIX = "vtuber_"


def _build_message_data(
    text: str,
    client_uid: str,
    message_id: Optional[str] = None,
    images: Optional[list] = None,
) -> Dict[str, Any]:
    """构造与 Napcat-adapter 同形的 message_data，仅当前输入，不携带历史。"""
    user_id = f"{USER_ID_PREFIX}{client_uid}"
    msg_id = message_id or str(int(time.time() * 1000))
    payload = {
        "message_info": {
            "platform": PLATFORM,
            "message_id": msg_id,
            "time": time.time(),
            "user_info": {
                "platform": PLATFORM,
                "user_id": user_id,
                "user_nickname": "观众",
                "user_cardname": "",
            },
            "format_info": {
                "content_format": ["text", "image", "emoji"],
                "accept_format": ["text", "image", "emoji"],
            },
            "additional_config": {},
        },
        "message_segment": {
            "type": "seglist",
            "data": [{"type": "text", "data": text}],
        },
        "raw_message": text,
    }
    if images:
        # 若需支持图片，可在此追加 message_segment.data 中的 image 段
        seg_data = payload["message_segment"]["data"]
        for img in images:
            if isinstance(img, dict) and img.get("data"):
                seg_data.append({"type": "image", "data": img["data"]})
    return payload


def _parse_reply_message(msg: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    从 MaiBot 下发的消息 JSON 中解析出回复文本与目标 client_uid。
    返回 (reply_text, client_uid)。client_uid 从 message_info.user_info.user_id 的 vtuber_ 前缀解析。
    """
    try:
        msg_info = msg.get("message_info") or {}
        user_info = msg_info.get("user_info") or {}
        target_user_id = user_info.get("user_id") or ""
        if not target_user_id.startswith(USER_ID_PREFIX):
            return None, None
        client_uid = target_user_id[len(USER_ID_PREFIX) :]
        # 回复文本：优先 processed_plain_text，否则从 message_segment 拼 text
        text = msg.get("processed_plain_text")
        if not text and msg.get("message_segment"):
            seg = msg["message_segment"]
            if isinstance(seg, dict) and seg.get("type") == "seglist":
                parts = []
                for item in (seg.get("data") or []):
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("data") or "")
                text = "".join(parts) if parts else None
        return (text or "").strip() or None, client_uid
    except Exception:
        return None, None


class MaiBotBridge:
    """连接 MaiBot WebSocket，发送 message_data（不携带历史），接收回复并投递到按 client_uid 的队列。"""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._ws = None
        self._receive_task: Optional[asyncio.Task] = None
        self._reply_queues: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    async def start(self) -> bool:
        if websockets is None:
            logger.error("MaiBot 桥接需要安装 websockets: pip install websockets")
            return False
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                extra_headers={"platform": PLATFORM},
                ping_interval=20,
                close_timeout=5,
            )
            self._closed = False
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info(f"MaiBot 桥接已连接: {self.ws_url}")
            return True
        except Exception as e:
            logger.error(f"MaiBot 桥接连接失败: {e}")
            return False

    async def stop(self) -> None:
        self._closed = True
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        async with self._lock:
            for q in self._reply_queues.values():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            self._reply_queues.clear()

    def _get_reply_queue(self, client_uid: str) -> asyncio.Queue:
        if client_uid not in self._reply_queues:
            self._reply_queues[client_uid] = asyncio.Queue()
        return self._reply_queues[client_uid]

    async def send_user_message(
        self,
        client_uid: str,
        text: str,
        images: Optional[list] = None,
    ) -> bool:
        """发送用户输入到 MaiBot（不携带历史）。"""
        if not self._ws or self._ws.closed:
            logger.warning("MaiBot 桥接未连接，无法发送")
            return False
        payload = _build_message_data(text, client_uid, images=images)
        try:
            await self._ws.send(json.dumps(payload))
            logger.debug(f"已向 MaiBot 发送消息 client_uid={client_uid} len={len(text)}")
            return True
        except Exception as e:
            logger.error(f"向 MaiBot 发送失败: {e}")
            return False

    async def wait_reply(self, client_uid: str, timeout: float = 120.0) -> Optional[str]:
        """等待该 client_uid 对应的 MaiBot 回复，超时返回 None。"""
        queue = self._get_reply_queue(client_uid)
        try:
            reply = await asyncio.wait_for(queue.get(), timeout=timeout)
            return reply
        except asyncio.TimeoutError:
            logger.warning(f"等待 MaiBot 回复超时 client_uid={client_uid}")
            return None
        except Exception as e:
            logger.debug(f"wait_reply 结束: {e}")
            return None

    async def _receive_loop(self) -> None:
        while not self._closed and self._ws and not self._ws.closed:
            try:
                raw = await self._ws.recv()
                data = json.loads(raw)
                text, client_uid = _parse_reply_message(data)
                if client_uid and text is not None:
                    queue = self._get_reply_queue(client_uid)
                    await queue.put(text)
                    logger.debug(f"收到 MaiBot 回复 client_uid={client_uid} len={len(text)}")
            except asyncio.CancelledError:
                break
            except json.JSONDecodeError as e:
                logger.debug(f"收到非 JSON: {e}")
            except Exception as e:
                if not self._closed:
                    logger.error(f"MaiBot 接收循环异常: {e}")
                break

    def release_client(self, client_uid: str) -> None:
        """会话结束后释放该 client 的队列（可选，避免堆积）。"""
        self._reply_queues.pop(client_uid, None)


# 全局单例，由启动时根据配置创建
_bridge: Optional[MaiBotBridge] = None


def get_maibot_bridge() -> Optional[MaiBotBridge]:
    return _bridge


def set_maibot_bridge(bridge: Optional[MaiBotBridge]) -> None:
    global _bridge
    _bridge = bridge
