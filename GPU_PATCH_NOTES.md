# GPU Patch (CUDA 11.8) — Unraid / GTX 1050 Ti

This patch switches your build to GPU-ready:

- **Docker** base: `nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04`
- **Dependencies** (requirements): Torch CUDA 11.8, and `opencv-python-cuda` for OpenCV CUDA
- Adds tool: `tools/gpu_smoke_test.py`

## Build & Run (Unraid)
1. Enable NVIDIA runtime for Docker and pass the GPU to the container.
2. Rebuild:
   ```bash
   docker build -t yourapp:gpu .
   docker run --gpus all -p 8080:8080 --env NVIDIA_VISIBLE_DEVICES=all yourapp:gpu
   ```
3. Verify GPU:
   ```bash
   docker exec -it <container> python3 /app/tools/gpu_smoke_test.py
   ```

## Notes
- Your app code isn't changed here; if you want to **force OpenCV ops to GPU**, swap CPU calls (e.g., `cv2.Canny`) to CUDA equivalents (e.g., `cv2.cuda.createCannyEdgeDetector`). Torch-based matching will automatically use GPU when `torch.cuda.is_available()` is True.
- Backups created: `*.bak` next to edited files.
