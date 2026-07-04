#!/usr/bin/env python3
# Minimal GPU smoke test for OpenCV + Torch
import sys
def main():
    torch_ok, cv2_ok = False, False
    cuda_count = 0
    try:
        import torch
        torch_ok = bool(torch.cuda.is_available())
    except Exception as e:
        print(f"[torch] ERROR: {e}")
    try:
        import cv2
        cuda_count = int(cv2.cuda.getCudaEnabledDeviceCount())
        cv2_ok = cuda_count > 0
    except Exception as e:
        print(f"[opencv] ERROR: {e}")
    print({"torch_cuda": torch_ok, "opencv_cuda_devices": cuda_count})
    sys.exit(0 if (torch_ok or cv2_ok) else 1)

if __name__ == "__main__":
    main()
