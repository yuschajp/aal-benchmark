# Archived — v1.0 GPT-5.6 Sol run (design-flawed dataset)

Run against AAL-D-007-v1.0.json / prompt-v1.0, before the Aug 19 2026 fix.

v1.0 always injected the error into the counterparty's copy, so
correct_im_amount was always the firm's total by construction, and the
prompt told the model exactly that ("the firm's total IM, taken from the
printed margin_breakdown"). The resulting 100.0% / 100.0% / 100.0%
(detection/value/escalation) scorecard reflects a copy-task, not genuine
dispute-resolution judgment, and should not be published or compared against
D-001-D-006.

Fixed in v1.1 (see d007_common.py module docstring): the injected error is
now assigned to firm or counterparty, balanced exactly 125/125 across the
corpus. GPT-5.6 Sol needs to be rerun against AAL-D-007-v1.1.json before any
result for this dataset is usable.
