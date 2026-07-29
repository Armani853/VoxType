# VoxType visual audit

| SCREEN | ISSUE | SEVERITY | FIX |
|---|---|---|---|
| Global | Semantic colors, spacing and radii coexist with scattered literal QSS | HIGH | Centralize the light/dark/overlay palettes and component roles in `ui/theme.py` |
| Global controls | Focus, pressed, disabled and table-selection states are inconsistent | HIGH | Define shared keyboard-focus and interaction states without animation |
| Buttons | Compact primary actions inherit the 52 px hero-button treatment; danger actions dominate | HIGH | Add explicit hero, compact-primary, secondary, ghost and subtle-danger roles |
| Main | Long microphone and target values can expand or visually unbalance cards | MEDIUM | Elide single-line values and retain the full value in a tooltip |
| Main | Status, hotkey hint and empty fragment lack distinct hierarchy | MEDIUM | Use semantic heading, key-hint and tertiary empty-state roles |
| Overlay | State colors and the dark surface are hard-coded locally | HIGH | Generate overlay QSS from shared semantic tokens |
| Completed overlay | Copy, Insert and Close have equal visual weight | HIGH | Promote the context-appropriate action and make Close tertiary |
| Onboarding | Pages use uneven vertical space and action hierarchy | MEDIUM | Normalize page/card spacing, compact progress copy and secondary repeat/refresh actions |
| Calibration | Completed “Repeat” remains a large primary action | MEDIUM | Return Repeat to secondary after a completed result; keep result status semantic |
| History | Native blue selection conflicts with VoxType indigo; empty state is too terse | HIGH | Use subtle primary selection and a two-line empty state |
| History | Read-only transcript looks like an editable form field | MEDIUM | Give it a dedicated content-surface role while preserving selection and scrolling |
| Settings | Fixed navigation can show a horizontal scrollbar at larger text/scales | HIGH | Disable horizontal scrolling and use a stable DPI-safe navigation width |
| Settings | Form sections and controls do not share a strong row baseline | MEDIUM | Normalize control heights, form spacing and page/card padding |
| Diagnostics | Self-test is styled like the main hero action; technical tables are visually raw | MEDIUM | Use compact-primary action, consistent row heights and semantic result states |
| Dialogs | QMessageBox and menu controls rely on incomplete global styling | MEDIUM | Add restrained shared dialog/menu/focus styling; keep native window chrome |
| DPI / resizing | Minimum-size and long-text behavior are not asserted across 100/125/150% | HIGH | Render key windows at all three scales and add lightweight size/elision tests |
| Tray / icon | Native tray behavior is sound; menu/icon need consistency verification only | LOW | Keep behavior and icon, add shared menu presentation |

## Final visual acceptance

| SCREEN | HIERARCHY | CONSISTENCY | READABILITY | SPACING | POLISH |
|---|---|---|---|---|---|
| Main | PASS | PASS | PASS | PASS | PASS |
| Dictation / completed overlay | PASS | PASS | PASS | PASS | PASS |
| First Run / calibration / model | PASS | PASS | PASS | PASS | PASS |
| History | PASS | PASS | PASS | PASS | PASS |
| Settings | PASS | PASS | PASS | PASS | PASS |
| Diagnostics / dialogs / tray | PASS | PASS | PASS | PASS | PASS |
