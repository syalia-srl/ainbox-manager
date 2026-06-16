import os
import socket


_VERSION = "0.1.0"


def get_status() -> dict:
    return {
        "hostname": socket.gethostname(),
        "version": _VERSION,
        "gpu": _gpu_info(),
        "backends": [],
        "models": [],
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
