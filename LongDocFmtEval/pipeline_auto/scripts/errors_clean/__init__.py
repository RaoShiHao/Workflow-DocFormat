"""Remediate step3d plan heading-gap violations (audit + cache cleanup).

Scripts:
  audit_plan_body_slots.py   — detect hard/soft rules on plan.json blocks
  cleanup_plan_body_slots.py — delete stale step3/step6 artifacts for bad sections
  audit_step6_content_gaps.py — broader plan + content layer audit (noisy)
"""
