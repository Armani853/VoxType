# VoxType — UX/UI audit

Date: 2026-11-05. Scope so far: real installed Windows EXE, onboarding steps 1–2 and the visible main window behind the wizard, observed with Computer Use. Remaining screens and interactions are not yet accepted.

## Observed issues

1. **Blocking first-run contradiction (VX-P1-002).** Step 2 displayed a selected microphone while saying no recording devices exist and disabled Next. A valid PortAudio index `0` was treated as empty. The rebuilt portable EXE passed a Computer Use retest through the calibration step.
2. **Misleading readiness (VX-P1-001).** The older baseline showed `Готово` before model availability was established. Current source contains a readiness correction, but the packaged UI has not yet been retested.
3. **First-run layout.** The 740×540 wizard leaves a large unused area below the short step 1 card and step 2 microphone card. On a 982×791 main window, this makes the setup feel sparse and moves the primary action far from the content. This is a visual observation, not a functional blocker. Assess across all eight steps before changing shared wizard geometry.
4. **Accessibility and clarity.** Step 2's message cannot be trusted while a device is visibly listed. It should reflect actual capture readiness. The combo and Next button are exposed to UI Automation, which is useful, but keyboard navigation and screen-reader announcements remain untested.
5. **Audio settings clipped (VX-P1-003).** In the rebuilt EXE, the audio page overflowed horizontally because the device combo's long current text set a large minimum width. The test microphone button and controls were partially cut off. A source fix is in place; packaged UI retest is pending.

## Next acceptance checks

- Retest the index `0` microphone path on the newly packaged EXE and proceed to calibration.
- Inspect steps 3–8, settings, history, diagnostics, tray, overlay, and keyboard navigation through Computer Use.
- Check clean-profile model absence and recovery action in the rebuilt UI.
- Measure layouts at available DPI and small window sizes before finalizing spacing changes.

No general UX readiness claim is made from the first two steps alone.
