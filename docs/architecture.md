# VisionPass architecture and privacy decisions

## Integration, not model development

`FaceRecognitionEncoder` is a boundary adapter. OpenCV decodes and normalizes
the image; the existing `face_recognition.face_encodings` implementation
produces an embedding. No training pipeline, custom neural network or claim of
independent ML-model development is present.

`DeterministicDemoEncoder` exists only so CI can verify enrollment, access,
review and deletion without downloading a heavy native CV stack. The active
backend is visible in `/health`.

## Data minimization

- Enrollment image bytes exist in `pending_image` only until the asynchronous
  worker succeeds or rejects the image.
- Access-check images are processed in request memory and never persisted.
- Audit details contain decisions and consent facts, never images or vectors.
- The delete endpoint erases the embedding; demo-data deletion also anonymizes
  the participant record while preserving non-identifying audit integrity.

## Decision policy

The nearest embedding distance is compared with two configuration values:

- at or below `match_threshold`: access is granted;
- inside the additional `review_margin`: no automatic decision, manual review;
- above both: access is denied without linking a participant.

This prevents an uncertain score from being presented as a confident identity.
Only one manual resolution can be stored for an attempt.

## Failure scenarios

| Failure | Behaviour |
|---|---|
| RabbitMQ unavailable | Enrollment event remains in transactional outbox |
| Worker receives the event twice | Terminal template status makes processing idempotent |
| No face or several faces | Enrollment is rejected; raw bytes are deleted |
| Uncertain distance | Attempt is routed to manual review |
| Unmatched image | Denied attempt does not expose nearest participant |
| Reviewer submits twice | Unique constraint returns `409 Conflict` |
| Deletion requested | Embedding and pending image are cleared and audited |
| Real CV packages missing | Adapter fails explicitly; no silent fallback to demo matching |
