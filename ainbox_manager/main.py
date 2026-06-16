import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import backends as back_module
from . import discovery as disc_module
from . import node

_discovery = disc_module.DiscoveryService()
_backends = back_module.BackendRegistry()
_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _backends.start()
    await _discovery.start()
    yield
    await _discovery.stop()
    await _backends.stop()


app = FastAPI(title="ainbox-manager", version="0.2.0", lifespan=lifespan)


def _find_for_model(model_name: str) -> tuple[dict, dict | None]:
    for b in _backends.all():
        if model_name in b.get("models", []):
            return {"ip": "localhost"}, b
    for peer in _discovery.peers():
        if not peer.get("reachable"):
            continue
        for b in peer.get("backends", []):
            if model_name in b.get("models", []):
                return peer, b
    return {"ip": "localhost"}, None


# ── Health ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Mesh ──────────────────────────────────────────────────────────────

@app.get("/mesh/status")
async def mesh_status():
    return node.get_status(_backends.all())


@app.get("/mesh/peers")
async def mesh_peers():
    return _discovery.peers()


@app.get("/mesh/peers/fragment", response_class=HTMLResponse)
async def peers_fragment(request: Request):
    return _templates.TemplateResponse(
        request=request,
        name="peers_fragment.html",
        context={"peers": _discovery.peers()},
    )


# ── Local backend management ──────────────────────────────────────────

@app.get("/backends")
async def list_backends():
    return _backends.all()


@app.post("/backends/ollama/pull")
async def ollama_pull(request: Request):
    body = await request.json()
    model = body.get("model", "")
    if not model:
        raise HTTPException(400, "model required")

    async def stream():
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream(
                "POST",
                f"{back_module.OLLAMA_URL}/api/pull",
                json={"name": model, "stream": True},
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
        await _backends.refresh()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.delete("/backends/ollama/models/{model_name:path}")
async def ollama_delete(model_name: str):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            "DELETE",
            f"{back_module.OLLAMA_URL}/api/delete",
            json={"name": model_name},
        )
    await _backends.refresh()
    return {"deleted": model_name, "ok": resp.status_code < 300}


# ── Peer proxy ────────────────────────────────────────────────────────

@app.api_route("/nodes/{ip}/{path:path}", methods=["GET", "POST", "DELETE"])
async def node_proxy(ip: str, path: str, request: Request):
    known = {p["ip"] for p in _discovery.peers() if p.get("reachable")}
    if ip not in known:
        raise HTTPException(404, f"Node {ip} not in reachable mesh peers")
    url = f"http://{ip}:{back_module.MESH_PORT}/{path}"
    body = await request.body() if request.method in ("POST", "DELETE") else None
    ct = request.headers.get("Content-Type", "application/json")

    if "pull" in path:
        async def proxy_stream():
            async with httpx.AsyncClient(timeout=600) as client:
                async with client.stream(request.method, url, content=body, headers={"Content-Type": ct}) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(proxy_stream(), media_type="application/x-ndjson")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(request.method, url, content=body, headers={"Content-Type": ct})
    return Response(resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))


# ── OpenAI-compatible API ─────────────────────────────────────────────

@app.get("/v1/models")
async def list_models():
    hostname = socket.gethostname()
    models = []
    for b in _backends.all():
        for m in b.get("models", []):
            models.append({"id": m, "object": "model", "backend": b["type"], "node": hostname})
    for peer in _discovery.peers():
        if not peer.get("reachable"):
            continue
        for b in peer.get("backends", []):
            for m in b.get("models", []):
                models.append({"id": m, "object": "model", "backend": b["type"],
                                "node": peer.get("hostname", peer["ip"])})
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "")
    is_stream = body.get("stream", False)

    peer, backend = _find_for_model(model)
    if backend is None:
        raise HTTPException(
            404,
            detail={"error": {"message": f"Model '{model}' not found in mesh", "type": "model_not_found"}},
        )

    if peer["ip"] == "localhost":
        target = f"{backend['url']}/v1/chat/completions"
    else:
        remote_base = backend["url"].replace("localhost", peer["ip"]).replace("127.0.0.1", peer["ip"])
        target = f"{remote_base}/v1/chat/completions"

    if is_stream:
        async def stream_resp():
            async with httpx.AsyncClient(timeout=600) as client:
                async with client.stream("POST", target, json=body) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(stream_resp(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(target, json=body)
    return Response(resp.content, status_code=resp.status_code, media_type="application/json")


# ── Dashboard ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "this_node": node.get_status(_backends.all()),
            "peers": _discovery.peers(),
        },
    )
