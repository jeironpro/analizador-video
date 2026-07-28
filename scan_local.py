import os
import sys
import json
import subprocess
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from video_analyzer import analyze_video, VideoAnalysisError

BASE = Path("/home/jeironpro/Vídeos/naruto_shippuden")
video_exts = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".mpeg", ".wmv"}

def scan_clamav(path):
    try:
        r = subprocess.run(
            ["clamscan", "--stdout", "--no-summary", "--quiet", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            return "limpio"
        if r.returncode == 1:
            return f"VIRUS: {r.stdout.strip()}"
        return f"error({r.returncode})"
    except Exception as e:
        return f"no disponible: {e}"

def analyze_file(path):
    size_mb = path.stat().st_size / 1024 / 1024
    result = {"archivo": str(path.relative_to(BASE)), "tamaño_mb": round(size_mb, 1)}

    clam = scan_clamav(path)
    result["clamav"] = clam

    try:
        info = analyze_video(str(path))
        result["valido"] = info.get("valid", False)
        result["contenedor"] = info.get("container", "?")
        result["streams"] = info.get("streams", [])
        if info.get("errors"):
            result["errores"] = info["errors"]
    except VideoAnalysisError as e:
        result["valido"] = False
        result["error"] = str(e)

    return result

total = 0
limpios = 0
con_errores = 0
con_virus = 0

for root, dirs, files in os.walk(BASE):
    for fname in sorted(files):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in video_exts:
            continue
        path = Path(root) / fname
        total += 1
        r = analyze_file(path)
        status = "✓" if r.get("valido") else "✗"
        clam = "🧹" if r.get("clamav") == "limpio" else "⚠" if "VIRUS" in r.get("clamav", "") else "·"
        print(f"{status}{clam} {r['tamaño_mb']:6.1f}MB  {r['archivo']}")
        if not r.get("valido"):
            con_errores += 1
            for e in r.get("errores", [r.get("error", "")]):
                print(f"         → {e}")
        if "VIRUS" in r.get("clamav", ""):
            con_virus += 1
            print(f"         → {r['clamav']}")
        if r.get("clamav") == "limpio":
            limpios += 1

print(f"\n{'='*50}")
print(f"Total: {total} | Limpios: {limpios} | Con errores: {con_errores} | Con virus: {con_virus}")
