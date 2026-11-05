# Customer experience audit

Acceptance candidate: commit `401beb1`, version 0.2.4. This is a partial independent Computer Use examination of the **installed** candidate. A later source-only fix (FA-005) has not been packaged, so these observations do not certify the latest source.

## Customer observations

The isolated installer completed. It began with “Select Destination Location” in English, followed by “Select Additional Tasks” and “Ready to Install”; the shortcut option itself was Russian. The mixed language and lack of an introductory explanation make the sale-facing installer less polished, although the path, disk requirement, summary, progress, and finish screens were understandable. The installed EXE and desktop shortcut were present, and the EXE SHA matched the compiled candidate.

The installed app showed a painted first-run wizard and responsive controls. A valid microphone with PortAudio index 0 was selected and Next worked. The calibration step displayed progress and then a noise warning with retry, change-device, details, and continue choices. Hardware profiling completed after a visible wait and recommended “Быстро.” The existing `small` model was verified locally. Step 6 requires two spoken phrases. With no voice supplied, both tests reported an empty result and Next stayed disabled. No claim about ASR accuracy or completed onboarding follows from this run.

The main window painted normally after the wizard was cancelled. Seven Settings tabs opened and were visually responsive. Start changed Ready to Listening, Stop changed Listening through Processing/Finishing back to Ready. The microphone captured uncontrolled ambient speech and local ASR displayed a fragment; that fragment is intentionally absent from this report and cannot establish accuracy. VoxType correctly withheld insertion into its own window and offered copy/retry actions. On reaching Ready, the audio queue tile still showed 4.9 seconds; this display defect is FA-005 and was fixed in source after the observed run.

The replacement `b598039` installer showed destination, tasks, summary, progress, and completion pages in Russian. Its installed app painted the first-run wizard. On the first live capture, PortAudio reported dropped input while the UI described it as a local recognition error. Retry returned to Listening and a later Stop completed; the audio queue correctly reset to 0.0 s at Ready. One ASR fragment took 24.738 s for 2.236 s of audio. The misleading error text has been repaired in source but has not been rebuilt or visually verified. The intermittent audio overflow and ASR latency remain open.

Disabling “Свернуть в системный трей” and saving caused X to terminate the installed process. A second launch from the installed EXE in Explorer painted the wizard and main window; after cancelling the still-incomplete wizard, X again ended the process. This verifies 2 of the required 100 cycles. The cancelled onboarding reappears on relaunch, as expected until step 6 is completed. Keyboard access, DPI, themes, tray recovery, controlled everyday dictation, and the full application matrix remain pending.

## Experience verdict

NOT READY. The full customer journey and critical reliability volume have not passed.
