# Submission Resubmit (重投)

Business-purpose: a submitter re-submits the SAME logical content of a
terminal, not-yet-published submission for a fresh review. This is deliberately
different from the PixivFlow schedule-recovery button (`sched_recover`, 再试一次/
放宽条件重试) and from the reviewer's refetch replacement (重抓): resubmit is a
**new pending review of the same content**, not a remote re-acquisition.

## Outcome vocabulary

| outcome                    | meaning | user message |
| ---                        | --- | --- |
| `resubmit_success`          | new pending review created in the same chain | 已重新提交审核。 |
| `resubmit_already_pending`  | chain head is still preparing/pending — no second task | 该作品已经提交审核，请等待处理。 |
| `resubmit_rejected`         | the resubmitted generation was later rejected (terminal) | 重新提交的作品审核未通过。 |
| `resubmit_failed`           | ONLY genuine system failure (DB/API/staging) | 重新提交失败，请稍后重试。 |

`resubmit_failed` is NEVER used for a business state. "Lost permission", a
still-pending chain and a rejection are business answers, not system errors.

## State machine

```
Resubmit Request
      ↓
Owner gate (verified human submitter only)
      ↓
Chain head status?
      ├─ preparing / pending ───────────► resubmit_already_pending (NO new row)
      ├─ publishing / approved / published ► resubmit_published (409, business)
      └─ rejected / failed / expired / cancelled
              ↓
      Create NEW pending review
      review_chain_id = same chain
      generation      = head.generation + 1
      supersedes_review_id = head.id     ← history preserved, nothing overwritten
              ↓
      resubmit_success
```

The old rejected row stays a durable row. A later rejection of the new
generation leaves `submission_v1 rejected` AND `submission_v2 rejected`.

## Idempotency

* A transport retry with the SAME `callbackKey` converges onto ONE attempt
  (the enqueue key is `resubmit:<uid>:<chain_id>:<uuid>` / callback key).
* A rapid double-click after the first request is already `preparing`/`pending`
  hits the already-pending gate and never creates a second task.
* Each **intentional** fresh click is a new business request (new key) which is
  correct: the requirement "quick double-click → one" is satisfied server-side
  by the already-pending gate, not by silently merging intentional retries.

## Where

* Service: `telepost/application/resubmit.py`
* Queue linkage: `telepost/application/review_queue.py` (`QueueCommand` +
  `_reserve` carry `review_chain_id` / `generation` / `supersedes_review_id`)
* Endpoint: `POST /api/v1/me/submissions/{review_id}/resubmit`
* Mini App: `SubmissionDetailPage` renders 重投 when `resubmit_available`.
* Rejection copy: `services/review_service.reject` uses the resubmit-specific
  message for a generation that superseded an earlier review.
