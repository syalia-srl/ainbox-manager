import socket

_VERSION = "0.2.0"


def get_status(backends: list[dict] | None = None) -> dict:
    backends = backends or []
    all_models = [m for b in backends for m in b.get("models", [])]
    return {
        "hostname": socket.gethostname(),
        "version": _VERSION,
        "gpu": _gpu_info(),
        "backends": backends,
        "models": all_models,
    }


def _gpu_info() -> list[dict]:
    try:
        import pynvml
        pynvml.nvmlInit()
        gpus = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            gpus.append({
                "index": i,
                "name": pynvml.nvmlDeviceGetName(h),
                "vram_total_gb": round(mem.total / 1e9, 1),
                "vram_used_gb": round(mem.used / 1e9, 1),
                "vram_free_gb": round(mem.free / 1e9, 1),
                "temp_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
            })
        return gpus
    except Exception:
        return []
