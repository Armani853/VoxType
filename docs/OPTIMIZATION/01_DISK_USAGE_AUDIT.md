# VoxType disk usage audit

Snapshot: 2026-11-05, root `C:\Dev\Projects\AI\Voice-recognizer`. Command: `.venv\Scripts\python.exe tools\audit_workspace.py . build\optimization\disk_inventory_before.json`. The scanner included hidden directories, did not follow symlink directories, and reported 0 read errors. Total: **3,906,902,417 bytes; 23,972 files; 2,772 directories**. This is the current snapshot, not the user-provided 2026-11-04 measurement. No files were removed in this measurement.

## Top-level usage

| Directory | Bytes | Files | Purpose | Category | Action |
|---|---:|---:|---|---|---|
| `.venv` | 1258757356 | 19471 | Python dev environment; runtime and dev packages | B | Keep until dependency audit; regenerate with bootstrap if needed |
| `build` | 1175140888 | 3002 | Nuitka intermediates plus stability evidence and test installs | B/E | Selectively clean only proven intermediates |
| `release` | 1018571090 | 20 | Versioned portable ZIP and installer archive | A | Keep unique releases |
| `dist` | 417239886 | 175 | Current portable and copied installer | A/D | Keep portable; copied installer may be removed after hash verification |
| `.mypy_cache` | 24891621 | 3 | Mypy cache | B | Safe to regenerate |
| `soak-results` | 6396791 | 70 | Soak evidence | A | Keep |
| `src` | 1695803 | 163 | Production source | A | Keep |
| `.git` | 1459887 | 766 | History | A | Keep |
| `tests` | 1220416 | 83 | Small project/configuration data | A/E | Keep pending review |
| `benchmark-results` | 841132 | 6 | Benchmark evidence | A | Keep |
| `tools` | 333709 | 28 | Small project/configuration data | A/E | Keep pending review |
| `docs` | 192790 | 33 | Small project/configuration data | A/E | Keep pending review |
| `.hypothesis` | 78235 | 97 | Small project/configuration data | A/E | Keep pending review |
| `.pytest_cache` | 28500 | 5 | Small project/configuration data | A/E | Keep pending review |
| `scripts` | 14166 | 7 | Small project/configuration data | A/E | Keep pending review |
| `.ruff_cache` | 12554 | 30 | Small project/configuration data | A/E | Keep pending review |
| `README_RU.md` | 5406 | 1 | Small project/configuration data | A/E | Keep pending review |
| `CHANGELOG.md` | 5195 | 1 | Small project/configuration data | A/E | Keep pending review |
| `PLAN.md` | 5038 | 1 | Small project/configuration data | A/E | Keep pending review |
| `TASKS.md` | 3948 | 1 | Small project/configuration data | A/E | Keep pending review |
| `packaging` | 2728 | 3 | Small project/configuration data | A/E | Keep pending review |
| `pyproject.toml` | 2241 | 1 | Small project/configuration data | A/E | Keep pending review |
| `LICENSE` | 1077 | 1 | Small project/configuration data | A/E | Keep pending review |
| `requirements.lock` | 940 | 1 | Small project/configuration data | A/E | Keep pending review |
| `AGENTS.md` | 617 | 1 | Small project/configuration data | A/E | Keep pending review |
| `README.md` | 210 | 1 | Small project/configuration data | A/E | Keep pending review |
| `.gitignore` | 193 | 1 | Small project/configuration data | A/E | Keep pending review |

## Largest second-level directories

| Directory | Bytes | Files |
|---|---:|---:|
| `.venv/Lib` | 1218946740 | 19396 |
| `build/stability-audit` | 684848593 | 454 |
| `build/nuitka` | 476749826 | 1802 |
| `dist/VoxType` | 336226472 | 174 |
| `release/VoxType-0.2.4-win64-portable.zip` | 123537950 | 1 |
| `release/VoxType-0.2.3-win64-portable.zip` | 122977889 | 1 |
| `release/VoxType-0.2.2-win64-portable.zip` | 122939509 | 1 |
| `release/VoxType-0.2.1-win64-portable.zip` | 122898977 | 1 |
| `release/VoxType-0.2.0-win64-portable.zip` | 122855161 | 1 |
| `dist/installer` | 81013414 | 1 |
| `release/VoxType-0.2.4-win64-setup.exe` | 81013414 | 1 |
| `release/VoxType-0.2.3-win64-setup.exe` | 80629804 | 1 |
| `release/VoxType-0.2.2-win64-setup.exe` | 80597583 | 1 |
| `release/VoxType-0.2.1-win64-setup.exe` | 80574999 | 1 |
| `release/VoxType-0.2.0-win64-setup.exe` | 80544819 | 1 |
| `.venv/Scripts` | 39810278 | 74 |
| `.mypy_cache/3.12` | 24891392 | 1 |
| `build/private-eval` | 10992026 | 734 |
| `soak-results/acceptance-full` | 3877096 | 5 |
| `build/packaging-assets` | 2343666 | 2 |
| `src/voxtype` | 1693077 | 157 |
| `soak-results/acceptance-forced` | 1555576 | 17 |
| `.git/objects` | 1410614 | 741 |
| `tests/unit` | 847557 | 55 |
| `benchmark-results/speech` | 836702 | 2 |
| `soak-results/quick-new` | 612416 | 34 |
| `build/product-rebuild` | 206777 | 10 |
| `soak-results/acceptance-final` | 170784 | 2 |
| `tools/__pycache__` | 156206 | 8 |
| `tests/integration` | 143076 | 6 |

## Top 50 files

| File | Bytes |
|---|---:|
| `.venv/Lib/site-packages/PySide6/Qt6WebEngineCore.dll` | 204754744 |
| `release/VoxType-0.2.4-win64-portable.zip` | 123537950 |
| `release/VoxType-0.2.3-win64-portable.zip` | 122977889 |
| `release/VoxType-0.2.2-win64-portable.zip` | 122939509 |
| `release/VoxType-0.2.1-win64-portable.zip` | 122898977 |
| `release/VoxType-0.2.0-win64-portable.zip` | 122855161 |
| `dist/installer/VoxType-0.2.4-win64-setup.exe` | 81013414 |
| `release/VoxType-0.2.4-win64-setup.exe` | 81013414 |
| `release/VoxType-0.2.3-win64-setup.exe` | 80629804 |
| `release/VoxType-0.2.2-win64-setup.exe` | 80597583 |
| `release/VoxType-0.2.1-win64-setup.exe` | 80574999 |
| `release/VoxType-0.2.0-win64-setup.exe` | 80544819 |
| `.venv/Lib/site-packages/PySide6/resources/qtwebengine_devtools_resources.debug.pak` | 75843536 |
| `.venv/Lib/site-packages/ctranslate2/ctranslate2.dll` | 59294720 |
| `build/stability-audit/installed-final-earlywatch-20261105/ctranslate2.dll` | 59294720 |
| `build/stability-audit/installed-test-20261105/ctranslate2.dll` | 59294720 |
| `dist/VoxType/ctranslate2.dll` | 59294720 |
| `dist/VoxType/VoxType.exe` | 49158656 |
| `build/stability-audit/installed-final-earlywatch-20261105/VoxType.exe` | 49150464 |
| `build/stability-audit/installed-test-20261105/VoxType.exe` | 49147392 |
| `.venv/Scripts/ruff.exe` | 32307712 |
| `.mypy_cache/3.12/cache.db` | 24891392 |
| `.venv/Lib/site-packages/PySide6/opengl32sw.dll` | 20639544 |
| `.venv/Lib/site-packages/numpy.libs/libscipy_openblas64_-43e11ff0749b8cbe0a615c9cf6737e0e.dll` | 20301824 |
| `build/stability-audit/installed-final-earlywatch-20261105/numpy.libs/libscipy_openblas64_-43e11ff0749b8cbe0a615c9cf6737e0e.dll` | 20301824 |
| `build/stability-audit/installed-test-20261105/numpy.libs/libscipy_openblas64_-43e11ff0749b8cbe0a615c9cf6737e0e.dll` | 20301824 |
| `dist/VoxType/numpy.libs/libscipy_openblas64_-43e11ff0749b8cbe0a615c9cf6737e0e.dll` | 20301824 |
| `.venv/Lib/site-packages/scipy.libs/libscipy_openblas-64eda39e79589aedb16f58e5547eb599.dll` | 20260864 |
| `.venv/Lib/site-packages/0aca9ce3d91742c5b361__mypyc.cp312-win_amd64.pyd` | 19514368 |
| `.venv/Lib/site-packages/av.libs/avcodec-62-984de33114b7fa384296817dec999c9d.dll` | 19266048 |
| `build/stability-audit/installed-final-earlywatch-20261105/av.libs/avcodec-62-984de33114b7fa384296817dec999c9d.dll` | 19266048 |
| `build/stability-audit/installed-test-20261105/av.libs/avcodec-62-984de33114b7fa384296817dec999c9d.dll` | 19266048 |
| `dist/VoxType/av.libs/avcodec-62-984de33114b7fa384296817dec999c9d.dll` | 19266048 |
| `.venv/Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd` | 17976632 |
| `build/stability-audit/installed-final-earlywatch-20261105/onnxruntime/capi/onnxruntime_pybind11_state.pyd` | 17976632 |
| `build/stability-audit/installed-test-20261105/onnxruntime/capi/onnxruntime_pybind11_state.pyd` | 17976632 |
| `dist/VoxType/onnxruntime/capi/onnxruntime_pybind11_state.pyd` | 17976632 |
| `.venv/Lib/site-packages/onnxruntime/capi/onnxruntime.dll` | 17320800 |
| `build/stability-audit/installed-final-earlywatch-20261105/onnxruntime/capi/onnxruntime.dll` | 17320800 |
| `build/stability-audit/installed-test-20261105/onnxruntime/capi/onnxruntime.dll` | 17320800 |
| `dist/VoxType/onnxruntime/capi/onnxruntime.dll` | 17320800 |
| `build/nuitka/__main__.build/blobs/__constant.bin.obj` | 14721784 |
| `build/nuitka/__main__.build/blobs/__constant.bin` | 14721682 |
| `.venv/Lib/site-packages/PySide6/avcodec-61.dll` | 13921592 |
| `.venv/Lib/site-packages/av.libs/libx265-efe48a158520a59ef99c0a0b3eb835ae.dll` | 12746240 |
| `build/stability-audit/installed-final-earlywatch-20261105/av.libs/libx265-efe48a158520a59ef99c0a0b3eb835ae.dll` | 12746240 |
| `build/stability-audit/installed-test-20261105/av.libs/libx265-efe48a158520a59ef99c0a0b3eb835ae.dll` | 12746240 |
| `dist/VoxType/av.libs/libx265-efe48a158520a59ef99c0a0b3eb835ae.dll` | 12746240 |
| `.venv/Lib/site-packages/PySide6/resources/qtwebengine_devtools_resources.pak` | 11609304 |
| `build/nuitka/__main__.build/module.huggingface_hub.hf_api.c` | 11066646 |

## SHA-256 confirmed duplicates above 20 MiB

- 59294720 bytes each, SHA-256 `a26134c47079896fc046317a3aea22a64df6b63b8c51272748cd2cc406d8d668`: `.venv/Lib/site-packages/ctranslate2/ctranslate2.dll`; `build/stability-audit/installed-final-earlywatch-20261105/ctranslate2.dll`; `build/stability-audit/installed-test-20261105/ctranslate2.dll`; `dist/VoxType/ctranslate2.dll`.
- 81013414 bytes each, SHA-256 `6f1af70a99ca55c3bdbdf2bbab429241c5d18c7a7c58a51f8df7c9433166682e`: `dist/installer/VoxType-0.2.4-win64-setup.exe`; `release/VoxType-0.2.4-win64-setup.exe`.

The same CTranslate2 DLL in the venv, test installations, and current portable is expected for independent runnable environments; it is not a reason to remove the runtime copy. The copied setup in `dist/installer` and `release` is byte-identical, but the versioned release archive is retained. No ASR model directories were found inside this repository; model storage under the user profile was outside this scan and was not modified.
