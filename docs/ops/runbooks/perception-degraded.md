# PerceptionBudgetBurn (ticket)

**Signal:** more than 14.4 % of actions could not be grounded by any tier, over both 1 h and 5 min. Those actions
went to a human as `grounding_failure`.

**Impact:** no wrong clicks (the verifier rejects them), but humans are absorbing work the agent should do, which eats
the ROI.

## Steps

1. Agent-internals dashboard → *Rejected by the verifier, by tier* and *Verified actions by tier*.
   * **Every tier rejecting** on one stage: **the portal changed its layout or labels.** Open a stuck task's replay:
     the step inspector shows each tier's point and the verifier's reason ("name 'Policy no.' does not contain
     'Policy number'"). Fix the target's expected label in the playbook (`playbooks/claims.py`) and redeploy. Stale
     cache entries are dropped after 3 failed verifications.
   * **Only `qwen` rejecting more than usual** with Claude rescuing: drift on the small model. Cost goes up, nothing
     else changes. Ticket for the model owner and attach examples from the replay.
   * **`qwen` errors** (`result="error"`): the vLLM endpoint is down. The chain skips to the next tier; check
     `QWEN_URL`.
2. For the tasks already waiting: once fixed, hand them back with **retry stage**.
