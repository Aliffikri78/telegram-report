import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from vision import ai_engine


def main():
    print("torch_version:", torch.__version__)
    print("torch_cuda_version:", torch.version.cuda)
    print("cuda_available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("gpu_name:", torch.cuda.get_device_name(0))
        x = torch.rand((4096, 4096), device="cuda")
        print("gpu_mean:", x.mean())
    print("ai_engine_status:", ai_engine.status())


if __name__ == "__main__":
    main()
