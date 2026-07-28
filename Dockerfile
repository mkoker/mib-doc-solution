# Offline submission image — see CONTRACT.md. Root FS runs read-only; all temp I/O
# must go to /tmp. No network at runtime: every dependency (incl. the RapidOCR ONNX
# models, which ship inside the rapidocr-onnxruntime wheel ~14 MB) is baked in here.
# Build runs online on VM100; runtime is fully offline.
FROM python:3.12-slim

WORKDIR /app

# libGL/glib for OpenCV (a RapidOCR dependency); slim keeps the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Pinned deps (versions verified locally 2026-07-26). opencv-python-headless replaces
# the full opencv-python that RapidOCR would otherwise pull (no GUI needed).
RUN pip install --no-cache-dir \
        pypdfium2==5.12.1 \
        numpy==2.5.1 \
        pillow==12.3.0 \
        rapidfuzz==3.14.5 \
        rapidocr-onnxruntime==1.2.3 \
        onnxruntime==1.28.0 \
    && pip uninstall -y opencv-python opencv-python-headless \
    && pip install --no-cache-dir opencv-python-headless==5.0.0.93
# ^ rapidocr pulls full opencv-python; both packages own the cv2/ dir, so uninstalling
#   one clobbers the other. Purge both, then install headless alone (no GUI deps).

# Warm the OCR model cache / fail the build early if models are missing.
RUN python3 -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

COPY run.sh /app/run.sh
COPY src/ /app/src/
RUN chmod +x /app/run.sh

# Single OCR thread per process; solution.py parallelises at the process level.
ENV OMP_NUM_THREADS=1 MIB_WORKERS=4

ENTRYPOINT ["/app/run.sh"]
