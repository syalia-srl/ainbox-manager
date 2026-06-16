import asyncio
import os
from typing import Optional

import httpx

MESH_PORT = int(os.getenv("MESH_PORT", "7860"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
VLLM_URL = os.getenv("VLLM_URL", "http://localhost:8000")
BACKEND_REFRESH = int(os.getenv("BACKEND_REFRESH", "15"))


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: list[dict] = []
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._refresh()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def all(self) -> list[dict]:
        return list(self._backends)

    async def refresh(self) -> None:
        await self._refresh()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(BACKEND_REFRESH)
            await self._refresh()

    async def _refresh(self) -> None:
        backends: list[dict] = []
        async with httpx.AsyncClient(timeout=2.0) as client:
            b = await self._probe_ollama(client)
            if b:
                backends.append(b)
            b = await self._probe_vllm(client)
            if b:
                backends.append(b)
        self._backends = backends

    async def _probe_ollama(self, client: httpx.AsyncClient) -> dict | None:
        try:
            tags = await client.get(f"{OLLAMA_URL}/api/tags")
            if tags.status_code != 200:
                return None
            models = [m["name"] for m in tags.json().get("models", [])]
            running: list[str] = []
            try:
                ps = await client.get(f"{OLLAMA_URL}/api/ps")
                if ps.status_code == 200:
                    running = [m["name"] for m in ps.json().get("models", [])]
            except Exception:
                pass
            return {"type": "ollama", "url": OLLAMA_URL, "models": models, "running": running}
        except Exception:
            return None

    async def _probe_vllm(self, client: httpx.AsyncClient) -> dict | None:
        try:
            resp = await client.get(f"{VLLM_URL}/v1/models")
            if resp.status_code != 200:
                return None
            models = [m["id"] for m in resp.json().get("data", [])]
            return {"type": "vllm", "url": VLLM_URL, "models": models}
        except Exception:
            return None
