# AInBox Engine — Design Spec

**Date:** 2026-06-30
**Status:** Approved

## Overview

The AInBox Engine is the single ML inference plane for all AInBox apps. It replaces the
scattered per-app inference code (warden's `ml_routes.py`/`streaming.py`, magpie's
`voice.py`) with a unified service that exposes a fully OpenAI-compatible API, routes
LLM requests across a Tailscale mesh of GPU nodes, and serves local transcription and
embeddings.

Apps (warden/lovelaice, magpie, future apps) point one env var at the engine and call
standard OpenAI endpoints. The engine has no auth — it is a trusted internal service on
the mesh. Warden retains all auth/permissions responsibility.

The repo is renamed from `ainbox-manager` to `ainbox-engine`; the Python package from
`ainbox_manager` to `ainbox_engine`.

---

## API Surface

All endpoints are OpenAI-compatible unless noted.

### LLM (existing, extended)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/v1/models` | Aggregated model list across all reachable mesh nodes |
| POST | `/v1/chat/completions` | Routes to best node; streaming + thinking tokens pass through |

### Embeddings (new)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/v1/embeddings` | Local fastembed; OpenAI-compatible request/response |

### Audio Transcription (new)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/v1/audio/transcriptions` | Multipart audio file → JSON `{text, language, duration_s}`; OpenAI-compatible |
| WS | `/v1/audio/transcriptions/stream` | Streaming PCM → JSON events `{committed, transient, is_final}`; harp HarpSession |

### Mesh / Ops (existing, unchanged)

| Method | Path |
|--------|------|
| GET | `/health` |
| GET | `/mesh/status` |
| GET | `/mesh/peers` |
| GET | `/mesh/peers/fragment` |
| GET | `/backends` |
| POST | `/backends/ollama/pull` |
| DELETE | `/backends/ollama/models/{name}` |
| ANY | `/nodes/{ip}/{path}` |
| GET | `/` (dashboard) |

---

## Backends

Each mesh node runs an engine instance. The engine probes its local backends on startup
and every `BACKEND_REFRESH` seconds (default 15 s).

### Backend types

**Ollama** (`OLLAMA_URL`, default `http://localhost:11434`)
- Probed via `/api/tags` (model list) and `/api/ps` (warm models).
- Used for: LLM chat completions, LLM embeddings (if an embedding model is loaded).
- Model management: pull/delete via existing `/backends/ollama/*` routes.
- Thinking tokens: supported natively via `think` param; pass through unchanged.

**vLLM** (`VLLM_URL`, default `http://localhost:8000`)
- Probed via `/v1/models`.
- Used for: LLM chat completions (high-concurrency alternative to Ollama).

**faster-whisper** (in-process, no sidecar)
- Lazy-loaded on first `/v1/audio/transcriptions` request; kept warm.
- Env: `ENGINE_WHISPER_MODEL` (default `small`), `ENGINE_WHISPER_DEVICE` (default `cpu`),
  `ENGINE_WHISPER_COMPUTE` (default `int8`).
- Batch endpoint: accepts any ffmpeg-readable audio format via temp file.
- Streaming endpoint: uses harp's `HarpSession` + `WardenAudioSource` pattern (imported
  from warden until extracted; see Migration).
- Reported in `/v1/models` as model id `whisper-<size>` with `backend: whisper`.

**fastembed** (in-process)
- Used for all `/v1/embeddings` requests — always local, no Ollama involvement.
- Lazy-loaded; kept warm.
- Env: `ENGINE_EMBED_MODEL` (default `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`),
  `ENGINE_EMBED_CACHE`.
- Reported in `/v1/models` as the model name with `backend: fastembed`.

### Backend probing summary

```
BackendRegistry.refresh():
  probe_ollama()   → {type: ollama, models: [...], running: [...]}
  probe_vllm()     → {type: vllm, models: [...]}
  probe_whisper()  → {type: whisper, models: ["whisper-small"]}   # always local
  probe_embed()    → {type: fastembed, models: ["..."]}            # always local
```

---

## Routing

### LLM chat completions

1. Search local backends first (Ollama, vLLM). If the requested model is found → use local.
2. Search reachable mesh peers. Score each candidate by free VRAM (`vram_free_gb` from
   the last probe). Pick the highest-scoring reachable node that has the model.
3. If no node has the model → 404 with OpenAI-style error body.

### Embeddings

Always local. No mesh routing — the embedding model stays loaded in the engine process.
If the local backend is unavailable → 503.

### Audio transcription

Always local. No mesh routing — the whisper model stays loaded in the engine process.
If the local backend is unavailable → 503.

---

## Migration Plan

### 1. Repo rename

- Rename `ainbox-manager` → `ainbox-engine` on GitHub.
- Rename Python package `ainbox_manager` → `ainbox_engine`.
- Update `pyproject.toml` name/version (bump to `0.3.0`).

### 2. New dependencies

Add to `pyproject.toml`:
- `faster-whisper>=1.2.1`
- `numpy>=2.0`
- `fastembed>=0.4`

Import harp as a library dependency (`harpio`, base package — no cli extra needed).

### 3. New source files

- `ainbox_engine/transcription.py` — `WhisperBackend`: lazy-load, `transcribe_file(bytes, suffix) -> dict`, probed as a backend.
- `ainbox_engine/embed.py` — `EmbedBackend`: lazy-load fastembed, `embed(texts) -> list[list[float]]`, probed as a backend.
- `ainbox_engine/audio_stream.py` — `EngineAudioSource` (same as warden's `WardenAudioSource`), `make_transcribe()`, `make_detector()`.
- Routes added to `ainbox_engine/main.py`: `POST /v1/embeddings`, `POST /v1/audio/transcriptions`, `WS /v1/audio/transcriptions/stream`.

### 4. Warden cleanup

Remove `warden/ml_routes.py` and `warden/streaming.py`. Update `warden/main.py` to drop
their registration. Remove `faster-whisper`, `fastembed`, `harpio` from warden's
`pyproject.toml`. Update env docs: `WARDEN_LLM_BASE_URL` → point at engine.

### 5. Magpie cleanup

Remove `magpie/voice.py`. Replace call sites with an httpx call to
`ENGINE_URL/v1/audio/transcriptions` (multipart form, same response shape). Add
`ENGINE_URL` env var to magpie.

### 6. Dashboard update

Add backend type badges for `whisper` and `fastembed` in `dashboard.html`.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESH_PORT` | `7860` | Engine listen port |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama backend |
| `VLLM_URL` | `http://localhost:8000` | vLLM backend |
| `BACKEND_REFRESH` | `15` | Backend probe interval (seconds) |
| `MESH_KNOWN_PEERS` | `` | Comma-separated IPs for static mesh peers |
| `MESH_PROBE_INTERVAL` | `30` | Peer probe interval (seconds) |
| `ENGINE_WHISPER_MODEL` | `small` | faster-whisper model size |
| `ENGINE_WHISPER_DEVICE` | `cpu` | Device: cpu / cuda / auto |
| `ENGINE_WHISPER_COMPUTE` | `int8` | Quantization: int8 / float16 / default |
| `ENGINE_EMBED_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | fastembed model |
| `ENGINE_EMBED_CACHE` | _(none)_ | fastembed model cache dir |

---

## Non-Goals (this spec)

- Auth on engine endpoints (trusted internal service; warden owns auth).
- Lovelaice / agent session management (stays in warden).
- TTS / speech synthesis.
- Multi-user isolation or per-request quotas.
- Automatic model download from the engine (Ollama pull is exposed; others are manual).
