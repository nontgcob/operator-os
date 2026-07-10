# Local Model Weights

Place local model weights here. Files in this directory are ignored by git.

For real SAM3 tracking through Ultralytics, copy or rename the SAM3 checkpoint to:

```text
models/sam3.pt
```

The Docker Compose SAM3 service mounts this folder read-only at `/app/models`, and the default local config points to:

```text
SAM3_CHECKPOINT_PATH=/app/models/sam3.pt
```

If your checkpoint uses a different filename, update `SAM3_CHECKPOINT_PATH` in `.env`.
