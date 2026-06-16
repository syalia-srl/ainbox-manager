from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import node, discovery as disc_module

_discovery = disc_module.DiscoveryService()
_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _discovery.start()
    yield
    await _discovery.stop()


app = FastAPI(title="ainbox-manager", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/mesh/status")
async def mesh_status():
    return node.get_status()


@app.get("/mesh/peers")
async def mesh_peers():
    return _discovery.peers()


@app.get("/v1/models")
async def list_models():
    models = []
    for peer in _discovery.peers():
        if peer.get("reachable"):
            for m in peer.get("models", []):
                models.append({**m, "_node": peer["hostname"]})
    return {"object": "list", "data": models}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    this_node = node.get_status()
    peers = _discovery.peers()
    return _templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"this_node": this_node, "peers": peers},
    )


@app.get("/mesh/peers/fragment", response_class=HTMLResponse)
async def peers_fragment(request: Request):
    """HTMX partial — peers table body only."""
    peers = _discovery.peers()
    return _templates.TemplateResponse(
        request=request,
        name="peers_fragment.html",
        context={"peers": peers},
    )
