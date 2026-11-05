# Final acceptance defects and fixes

Candidate commit `401beb1`. No candidate defect is marked verified until the installed EXE reproduces the repaired behavior through Computer Use.

| ID | Severity | Candidate evidence | Root cause | Fix | Rebuild/retest status |
|---|---|---|---|---|---|
| FA-001 | Investigating | `scripts/test.ps1` completed 259/259 selected tests but emitted Windows fatal exception code `0x8001010d` during a GUI performance test | Unknown | None yet | Candidate GUI investigation pending |
| FA-002 | Release gate | Historical user report of blank/white GUI and AppHang; root cause not proven | Unknown | Prior hardening committed | 100 installed candidate cycles pending |
| FA-003 | P2 UX | Computer Use installer pages on 2026-11-05 were English while shortcut choice was Russian | Inno Setup script does not select a Russian language file | Not fixed; app binaries unaffected | Installer rebuild and Computer Use retest required |
| FA-004 | Environment gate | Installed onboarding step 6 requires RU/EN speech; both no-voice attempts returned empty and Next remained disabled | No controlled physical voice was supplied to the selected microphone | Not a proven product defect | Real-speech retest required |
| FA-005 | P2 UI accuracy | During an installed 7-second capture, GUI returned to Ready yet still displayed 4.9 seconds of queued audio | `_last_stats` was captured when the audio dispatcher stopped, before the ASR pipeline acknowledged the remaining leases | Refresh `_last_stats` after pipeline and delivery completion; integration assertion added | Source lint and 259 tests pass; **not rebuilt or Computer Use verified** |
| FA-006 | Test tooling | A `sky.launch_app` request for the isolated installed EXE launched an old test installation, as confirmed by `Win32_Process.ExecutablePath` | Computer Use app resolution ambiguous between identically named installations; product fault not established | Launched the exact installed EXE from its folder through Explorer UI and verified its process path | Second installed launch/close verified; exclude misrouted launch from product cycle count |

Further findings, screenshots, trace paths, and exact fix commits will be appended here during acceptance.
