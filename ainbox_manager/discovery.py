import asyncio
import json
import os
import subprocess
from typing import Optional

import httpx

MESH_PORT = int(os.getenv("MESH_PORT", "7860"))
MESH_KNOWN_PEERS = [
    ip.strip()
    for ip in os.getenv("MESH_KNOWN_PEERS", "").split(",")
    if ip.strip()
]
PROBE_INTERVAL = int(os.getenv("MESH_PROBE_INTERVAL", "30"))


class DiscoveryService:
    def __init__(self) -> None:
        self._peers: dict[str, dict] = {}
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._probe_all()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def peers(self) -> list[dict]:
        return sorted(self._peers.values(), key=lambda p: p["hostname"])

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(PROBE_INTERVAL)
            await self._probe_all()

    async def _candidates(self) -> list[str]:
        ips = set(MESH_KNOWN_PEERS)
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                status = json.loads(result.stdout)
                for peer in status.get("Peer", {}).values():
                    if peer.get("Online") and peer.get("TailscaleIPs"):
                        ips.add(peer["TailscaleIPs"][0])
        except Exception:
            pass
        return list(ips)

    async def _probe_all(self) -> None:
        candidates = await self._candidates()
        async with httpx.AsyncClient(timeout=3.0) as client:
            await asyncio.gather(
                *[self._probe(client, ip) for ip in candidates],
                return_exceptions=True,
            )

    async def _probe(self, client: httpx.AsyncClient, ip: str) -> None:
        try:
            resp = await client.get(f"http://{ip}:{MESH_PORT}/mesh/status")
            if resp.status_code == 200:
                data = resp.json()
                self._peers[ip] = {"ip": ip, "reachable": True, **data}
                return
        except Exception:
            pass
        if ip in self._peers:
            self._peers[ip]["reachable"] = False
