# Environment changes

This file records non-source changes made while validating and packaging
VoxType on the target Windows machine.

## 2026-07-27

- Installed and verified the pinned local `faster-whisper` large-v3 model
  through VoxType `ModelManager`.
  - Location:
    `%LOCALAPPDATA%\VoxType\VoxType\models\large-v3`
  - `model.bin` size: `3,087,284,237` bytes
  - `model.bin` SHA-256:
    `69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1`
  - Model revision:
    `edaa852ec7e145841d8ffdb056a99866b5f0a478`
- Created an isolated, private benchmark virtual environment under
  `build/private-eval/onnx-asr-venv`.
- Installed `onnx-asr 0.12.0` and its CPU/Hugging Face extras only in that
  private benchmark environment.
- Downloaded private benchmark copies of GigaAM v3 CTC INT8 and GigaAM v3 E2E
  CTC INT8 under `build/private-eval/gigaam-model`. They are not production
  dependencies and are not included in release artifacts.
- No cloud speech-to-text service, telemetry, local HTTP service, or paid API
  was added.
- No Windows security setting was disabled.

### Vulkan experiment

- Downloaded the official LunarG Vulkan SDK 1.4.350.0 installer from
  `sdk.lunarg.com`.
  - Installer size: `324,012,984` bytes.
  - SHA-256:
    `855b27ba05d2d8119c5114c5d4ff870ca38f2c632b11e1bb9923b9b7e6ecfe7b`.
- Installed only the core SDK in official `copy_only=1` mode under
  `C:\VulkanSDK\1.4.350.0`. The existing Vulkan runtime and AMD display driver
  were not replaced.
- Updated the private benchmark checkout of `whisper.cpp` from v1.8.1 to the
  official v1.9.1 tag (`f049fff`).
- Built `whisper.cpp` Release x64 with `GGML_VULKAN=ON` using:
  - CMake 4.1.1;
  - Visual Studio 2022 Community 17.11.35303.130;
  - MSVC 19.41.34120;
  - Windows SDK 10.0.22621.0;
  - Vulkan SDK 1.4.350.0.
- The short build directory `C:\VoxTypeBuild\whisper-vulkan-v1.9.1` was needed
  because MSBuild file tracking exceeded the Windows path-length limit under
  the repository's nested build directory.
- Vulkan device probe:
  - AMD Radeon(TM) Graphics, integrated/UMA, FP16 available;
  - device API 1.2.150;
  - AMD proprietary driver 20.40.16 / Windows driver 27.20.14016.13,
    dated 2020-10-27.
- Downloaded the official multilingual `ggml-small.bin` model for the private
  experiment:
  - size: `487,601,967` bytes;
  - SHA-256:
    `1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`.
- Quantized it locally with the official `whisper-quantize` tool to Q5_0:
  - size: `175,209,663` bytes;
  - SHA-256:
    `23ad5215d37677f8eb8cbc41b4d0d4e451229f811be5206ff116dd9337eba748`.
- Vulkan inference was rejected for production. On the same 30-second Russian
  input, CPU-only produced coherent Russian text at wall RTF `0.442`, while
  Vulkan produced corrupted multilingual output at wall RTF `0.237`. The
  corruption was confirmed in the UTF-8 output file and was not a console
  encoding artifact. No Vulkan backend was integrated.
