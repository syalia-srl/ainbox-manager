# AInBox Engine — reconciliation against live stack state

**Date:** 2026-07-01
**Reconciles:** [`2026-06-30-ainbox-engine-design.md`](2026-06-30-ainbox-engine-design.md) (Approved)
**Method:** live probes of demos/charizard/registry + file:line inventory of ainbox-manager, warden, magpie, superbot, peacock, harp on 2026-07-01.

## TL;DR

The engine spec is **directionally sound and mostly additive to an already-running service** — but its framing is **one release stale**, and its central security premise is **already violated in production**.

- The engine base (`ainbox-manager`) is **already live and doing the LLM half** of the spec — `/v1/models` + `/v1/chat/completions` + mesh/backends, v0.2.0, running on demos and reachable.
- The spec says it "replaces scattered per-app inference code (warden's `ml_routes.py`/`streaming.py`, magpie's `voice.py`)." **That scattering was already removed on 2026-06-26**: warden *is* the centralized ML service today, and magpie/superbot/peacock are already thin HTTP clients to it. So this is a **lateral move (warden → engine)**, not a de-scattering. The migration is smaller and lower-risk than the spec implies — most of the "new" engine code already exists in warden and can be lifted almost verbatim.
- **Security:** the spec assumes the engine is a "no-auth trusted internal service." The manager is currently **published on `0.0.0.0:7860` and publicly reachable at `https://manager.syalia.dev/v1/models` → HTTP 200, no auth.** Adding CPU-heavy `whisper`/`fastembed` to that same open endpoint turns a free-LLM-inference exposure into a free-transcription/embedding DoS surface. **This must be closed before, or as part of, the ML move** — it is the highest-priority finding here.

## Live deploy topology (measured 2026-07-01)

| Target | What runs | Reality |
|---|---|---|
| **demos VPS** (`*.syalia.dev` prod) | full stack + `ainbox-manager-1` (v0.2.0, healthy, 4d) + `ainbox-ollama-1` (llama3.2:3b, gemma3:4b, qwen3:8b) | `gpu: []` — **CPU-only**. Manager routes to local ollama. Mesh `MESH_KNOWN_PEERS=100.64.0.3,.8,.10` configured but **peers not reachable** in `/mesh/status` (single-node in practice). |
| **warden (demos)** | `ainbox-warden-1` healthy | LLM points at **OpenRouter** (`WARDEN_LLM_BASE_URL=https://openrouter.ai/api/v1`, `claude-haiku-4-5`) — **not** the manager. Bakes whisper+fastembed (`HF_HOME`, `WARDEN_EMBED_CACHE` present). So **warden ↔ manager are decoupled in prod**: manager+ollama run but warden bypasses them. |
| **charizard** (dev rig) | Ollama `10.6.125.217:11434` / `charizard.local` | One model: **`qwen3.5:9b`** (Q4_K_M, 9.7B, vision, 262k ctx). Dev warden/manager point here. Reachable now. |
| **registry.syalia.dev** | 6 images incl. `ainbox-manager` | **No OCI models** — the sibling self-hosted-models spec is unimplemented. Engine will bake whisper/fastembed into its image (~1.4 GB, same tradeoff warden carries now). |
| **airgapped Windows appliance** | single box, local ollama, no-login | Engine must be the local ML plane; warden's LLM must point at engine→ollama (offline). Image bytes matter (Cuba throttle → ties to fast-fetch spec). |
| **ainbox-os** (Fedora bootc) | own Ollama daemon + panel baked | `tasks.md`: "AI-n-Box stack inside server/desktop **(requires engine RPM)**" — this target wants the engine as a **system package**, an axis the spec doesn't mention. |
| **home GPU cluster** (5090s) | feasibility only, **not built** | The mesh + VRAM-routing has **no live consumer yet**. The 3 tailscale peers in prod config are placeholders. → mesh/VRAM work is low near-term value; ML consolidation is the high-value part. |

## Component reconciliation (spec claim → measured reality)

### ainbox-manager (engine base) — `ainbox_manager/` v0.2.0
| Spec treats as | Reality | file:line |
|---|---|---|
| LLM routes "existing, extended" | ✅ **EXISTS**: `/v1/models`, `/v1/chat/completions`, `/mesh/*`, `/backends/*`, `/nodes/{ip}/{path}`, `/health`, `/` dashboard | `ainbox_manager/main.py:46–199` |
| Ollama + vLLM backends | ✅ EXISTS: `BackendRegistry`, probes ollama `/api/tags`+`/api/ps`, vllm `/v1/models` | `backends.py:13–73` |
| VRAM-scored mesh routing | ⚠️ **GAP the spec hides** — routing is **naive first-found**, no VRAM scoring | `main.py:31–41` |
| `/v1/embeddings`, `/v1/audio/transcriptions`, `WS …/stream` | ❌ **absent** — genuinely new (but liftable from warden) | — |
| whisper/fastembed/numpy/harpio deps | ❌ absent (`fastapi/uvicorn/httpx/jinja2/pynvml` only) | `pyproject.toml:5–11` |
| dashboard whisper/fastembed badges | ❌ absent (ollama+vllm badges only) | `templates/dashboard.html:68` |
| repo/pkg = `ainbox-engine`/`ainbox_engine` | ❌ still `ainbox-manager`/`ainbox_manager` | `pyproject.toml:1–5` |

### warden — the ML code the spec wants to DELETE already exists and works
- `warden/ml_routes.py` (199 lines): `POST /api/transcribe`, `WS /api/transcribe/stream`, `POST /api/embed` — service-token auth (`_verify_service_token_any_namespace`), faster-whisper `small` + fastembed MiniLM, env `WARDEN_WHISPER_*`/`WARDEN_EMBED_*`.
- `warden/streaming.py` (75 lines): `WardenAudioSource` + `make_transcribe()`/`make_segments()`/`make_detector()` over **harp** (`HarpSession`, `harp.vad.SileroDetector`). **This is the exact `audio_stream.py` the spec says to write — it already exists; the task is a MOVE, not a build.**
- deps present: `faster-whisper>=1.0`, `fastembed>=0.8`, `harpio>=0.9.0` (`pyproject.toml:23–25`); models baked (`Dockerfile:22–27`); **no system ffmpeg** (PyAV decode — corrects the older warden-ML design).
- warden LLM (`/api/llm/complete` + agent runtime) is a **separate relay**, defaults to OpenRouter (`llm_routes.py`, `config.py`). Spec step 4 "`WARDEN_LLM_BASE_URL` → engine" is a **config change with real consequences** (see below), not a doc tweak.

### magpie / superbot / peacock — already thin clients, not local inference
All three already reach ML over HTTP with a **uniform** env pattern `WARDEN_BASE_URL` + `WARDEN_SERVICE_TOKEN`:
- magpie: `voice.py:52` → warden `/api/transcribe`; `index.py:59` → warden `/api/embed` (degrades to BM25 if down). Optional smart-format LLM pass via `WARDEN_LLM_*`.
- superbot: `transcribe.py:43` → `/api/transcribe`; `rag.py:36` `_warden_embed` (hard-fails if unavailable).
- peacock: `main.py:143` → `/api/transcribe` proxy; **no embeddings**.
- **No `faster-whisper`/`fastembed` in any of the three** — the 2026-06-26 migration already removed local ML. So "remove magpie `voice.py`" really means **"repoint the existing httpx call from `WARDEN_BASE_URL` to `ENGINE_URL`"**, not delete-local-inference.

### harp — consumable as a library today
`harpio` v0.9.0 (import `harp`); `HarpSession` API is stable (`src/harp/session.py:19`), `AudioSource` is a public Protocol (`src/harp/audio.py:17`), engine is faster-whisper. Base package minimal (faster-whisper+numpy), `cli` extra not needed. **warden already depends on it**, so the engine inherits a proven integration.

## The two decisions this surfaces

1. **Auth on the engine — resolve before adding ML.** The spec's "no auth, trusted mesh" is defensible *only if 7860 is never publicly reachable*. Today it is (`manager.syalia.dev → 200`). Options:
   - **(A, recommended)** Make the engine strictly internal: drop the `0.0.0.0:7860` host publish, remove the `manager.syalia.dev` caddy route, reach it only over the compose network + tailnet. Then no-auth holds and the ML move is clean.
   - **(B)** Keep warden as the authenticating front for `/api/transcribe`+`/api/embed` (apps keep talking to warden, warden proxies to the engine). Contradicts the engine's no-auth design and keeps warden in the ML path — but preserves the current auth boundary with zero app changes.
   Recommendation: **A**, and gate the ML endpoints behind it.

2. **Decouple the rename from the ML move.** `ainbox-manager → ainbox-engine` touches the GitHub repo (breaks clones/CI), the registry image name, the ainbox compose (`apps/manager/…`, service `manager`, `manager.ainbox.local`), and ainbox-os's expected "engine RPM." That is coordination cost with no functional payoff. **Ship the ML endpoints under the existing `ainbox-manager` name first; rename as a separate, scheduled cutover.**

## Revised migration ordering (grounded)

1. **Close the exposure** (decision 1A): unpublish 7860, drop the public caddy route. *(Prereq for everything else; also fixes a live open-LLM endpoint today.)*
2. **Lift ML into the engine**: copy warden's `ml_routes.py` bodies + `streaming.py` into the engine as `/v1/embeddings`, `/v1/audio/transcriptions`, `WS …/stream` + `embed.py`/`transcription.py`/`audio_stream.py`; add `faster-whisper`/`fastembed`/`harpio`/`numpy`; bake models; dashboard badges. (New code is minimal — mostly a move + OpenAI-shape adaptation of request/response.)
3. **Repoint the 3 apps**: introduce `ENGINE_URL`, swap the transcribe/embed httpx targets from `WARDEN_BASE_URL` → `ENGINE_URL` (apps keep `WARDEN_BASE_URL` for auth/agent/mail/LLM-smart-format). Verify magpie graceful-degrade + superbot hard-dep paths.
4. **Retire warden ML**: delete `ml_routes.py`/`streaming.py` + their tests, drop the 3 deps, slim the Dockerfile, drop the model bake. *(Only after step 3 is verified end-to-end.)*
5. **Optional/deferred**: warden LLM → engine (`WARDEN_LLM_BASE_URL`) — **only meaningful on the appliance/offline target**; on demos warden intentionally uses OpenRouter, so this is per-deploy config, not a global flip.
6. **Deferred**: VRAM-scored routing (no live consumer until the home GPU boxes join); repo/package rename (separate cutover); ainbox-os engine RPM.

## What the spec got right
Mesh-routed OpenAI-compatible surface, harp for streaming, in-process fastembed/whisper, warden-keeps-auth division of labor, and the non-goals (no TTS, no agent sessions). The bones are correct; this doc just grounds the *starting point* (further along than the spec assumes) and flags the *exposure* (worse than the spec assumes).
