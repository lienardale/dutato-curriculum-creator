# Planted-defect fixtures

Reserved for **frozen anchors**: the human-approved pilot lesson and its verdict files
(`rubrics/critique_protocol.md` §6), committed once per curriculum family so critic prompts can
be re-checked against a known-good reference whenever a rubric changes.

The planted-defect fixtures themselves live **inside the stage test files**
(`tests/test_<stage>.py`): each test builds a clean synthetic artifact in `tmp_path`, plants one
defect, and asserts the `check_id` the deterministic gate must raise — so the clean baseline
doubles as a regression guard against false positives. See `tests/test_critique.py` for the
critique harness (withholding, probe matrix, fidelity forcing, repair gates, summary merging).

Nothing in this folder is read by the pipeline scripts or by the tests.
