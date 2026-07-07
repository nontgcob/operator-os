from __future__ import annotations

import asyncio
import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Protocol

TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_OVERLAY_COLOR = "#67A552"


@dataclass(frozen=True)
class TrackingJob:
    tracking_job_id: str
    session_id: str
    video_id: str
    timestamp: float
    frame_data_url: str
    question: str
    segmentation_prompt: str = ""
    annotations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingBackendStatus:
    backend: str
    ready: bool
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class TrackingBackendConfig:
    backend: str = "sam3"
    allow_simulation_fallback: bool = False
    checkpoint_path: Path | None = None
    allow_hf_download: bool = False
    video_root: Path = Path("data/video")
    device: str | None = None
    max_frames: int = 20
    frame_interval_seconds: float = 1.0
    simulation_steps: int = 10
    simulation_delay_seconds: float = 0.35

    @classmethod
    def from_env(cls) -> "TrackingBackendConfig":
        checkpoint = os.getenv("SAM3_CHECKPOINT_PATH")
        return cls(
            backend=os.getenv("SAM3_TRACKING_BACKEND", "sam3").strip().lower(),
            allow_simulation_fallback=_env_flag("SAM3_ALLOW_SIMULATION_FALLBACK"),
            checkpoint_path=Path(checkpoint).expanduser() if checkpoint else None,
            allow_hf_download=_env_flag("SAM3_ALLOW_HF_DOWNLOAD"),
            video_root=Path(os.getenv("SAM3_VIDEO_ROOT", "data/video")).expanduser(),
            device=os.getenv("SAM3_DEVICE") or None,
            max_frames=max(1, _env_int("SAM3_MAX_PROPAGATION_FRAMES", 20)),
            frame_interval_seconds=max(0.001, _env_float("SAM3_FRAME_INTERVAL_SECONDS", 1.0)),
            simulation_steps=max(1, _env_int("SAM3_SIMULATION_STEPS", 10)),
            simulation_delay_seconds=max(0.0, _env_float("SAM3_SIMULATION_DELAY_SECONDS", 0.35)),
        )


class TrackingBackend(Protocol):
    name: str

    def status(self) -> TrackingBackendStatus:
        ...

    async def track(self, job: TrackingJob) -> AsyncIterator[dict[str, Any]]:
        ...


class UnavailableTrackingBackend:
    name = "unavailable"

    def __init__(self, backend: str, code: str, message: str) -> None:
        self._status = TrackingBackendStatus(
            backend=backend,
            ready=False,
            code=code,
            message=message,
        )

    def status(self) -> TrackingBackendStatus:
        return self._status

    async def track(self, job: TrackingJob) -> AsyncIterator[dict[str, Any]]:
        yield tracking_error_payload(
            code=self._status.code or "tracking_backend_unavailable",
            message=self._status.message or "Tracking backend is unavailable.",
            backend=self._status.backend,
        )


class SimulationTrackingBackend:
    name = "simulation"

    def __init__(self, steps: int = 10, delay_seconds: float = 0.35) -> None:
        self.steps = max(1, steps)
        self.delay_seconds = max(0.0, delay_seconds)

    def status(self) -> TrackingBackendStatus:
        return TrackingBackendStatus(backend=self.name, ready=True)

    async def track(self, job: TrackingJob) -> AsyncIterator[dict[str, Any]]:
        overlays: list[dict[str, Any]] = []
        for step in range(1, self.steps + 1):
            overlays.extend(_simulated_overlay(job.timestamp + (step * 0.5), float(step)))
            yield {
                "done": False,
                "progress": min(99, round((step / self.steps) * 100)),
                "overlays": overlays,
                "backend": self.name,
            }
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)

        yield {
            "done": True,
            "progress": 100,
            "overlays": overlays,
            "backend": self.name,
        }


class Sam3TrackingBackend:
    name = "sam3"

    def __init__(self, config: TrackingBackendConfig) -> None:
        self.config = config
        self._model: Any | None = None
        self._model_lock = asyncio.Lock()

    def status(self) -> TrackingBackendStatus:
        if importlib.util.find_spec("sam3") is None:
            return TrackingBackendStatus(
                backend=self.name,
                ready=False,
                code="sam3_dependency_missing",
                message="Python package 'sam3' is not installed in this environment.",
            )
        if self.config.checkpoint_path is not None and not self.config.checkpoint_path.exists():
            return TrackingBackendStatus(
                backend=self.name,
                ready=False,
                code="sam3_checkpoint_missing",
                message=f"SAM3 checkpoint does not exist: {self.config.checkpoint_path}",
            )
        if self.config.checkpoint_path is None and not self.config.allow_hf_download:
            return TrackingBackendStatus(
                backend=self.name,
                ready=False,
                code="sam3_checkpoint_missing",
                message=(
                    "SAM3_CHECKPOINT_PATH is not configured. Set a checkpoint path, or set "
                    "SAM3_ALLOW_HF_DOWNLOAD=true to allow the sam3 package to download weights."
                ),
            )
        return TrackingBackendStatus(backend=self.name, ready=True)

    async def track(self, job: TrackingJob) -> AsyncIterator[dict[str, Any]]:
        status = self.status()
        if not status.ready:
            yield tracking_error_payload(
                code=status.code or "sam3_unavailable",
                message=status.message or "SAM3 tracking backend is unavailable.",
                backend=status.backend,
            )
            return

        video_path = self._video_path(job.video_id)
        if not video_path.exists():
            yield tracking_error_payload(
                code="sam3_video_missing",
                message=f"Video source for {job.video_id} was not found at {video_path}.",
                backend=self.name,
            )
            return

        async for update in self._track_in_worker(job, video_path):
            yield update

    def _video_path(self, video_id: str) -> Path:
        return self.config.video_root / video_id / "source.mp4"

    async def _load_model(self) -> Any:
        async with self._model_lock:
            if self._model is not None:
                return self._model

            def load_model() -> Any:
                from sam3.model_builder import build_sam3_video_model

                kwargs: dict[str, Any] = {
                    "checkpoint_path": str(self.config.checkpoint_path)
                    if self.config.checkpoint_path
                    else None,
                    "load_from_HF": self.config.allow_hf_download,
                }
                if self.config.device:
                    kwargs["device"] = self.config.device
                return build_sam3_video_model(**kwargs)

            self._model = await asyncio.to_thread(load_model)
            return self._model

    async def _track_in_worker(self, job: TrackingJob, video_path: Path) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        def put(item: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        async def load_model() -> Any:
            try:
                return await self._load_model()
            except Exception as exc:  # pragma: no cover - depends on external SAM3 runtime.
                return exc

        model_or_error = await load_model()
        if isinstance(model_or_error, Exception):
            yield tracking_error_payload(
                code="sam3_model_load_failed",
                message=str(model_or_error),
                backend=self.name,
            )
            return

        def run() -> None:
            try:
                for update in self._run_sam3_sync(model_or_error, job, video_path):
                    put(update)
            except Exception as exc:  # pragma: no cover - depends on external SAM3 runtime.
                put(
                    tracking_error_payload(
                        code="sam3_tracking_failed",
                        message=str(exc),
                        backend=self.name,
                    )
                )
            finally:
                put(sentinel)

        worker = asyncio.create_task(asyncio.to_thread(run))
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item
        finally:
            await worker

    def _run_sam3_sync(
        self,
        model: Any,
        job: TrackingJob,
        video_path: Path,
    ) -> Iterator[dict[str, Any]]:
        inference_state = model.init_state(resource_path=str(video_path))
        frame_idx = max(0, round(job.timestamp / self.config.frame_interval_seconds))
        boxes = annotation_boxes_xywh(job.annotations)
        prompt = job.segmentation_prompt or job.question
        box_labels = [1] * len(boxes) if boxes else None
        prompt_boxes = boxes or None

        _, outputs = model.add_prompt(
            inference_state,
            frame_idx=frame_idx,
            text_str=prompt,
            boxes_xywh=prompt_boxes,
            box_labels=box_labels,
        )

        overlays = outputs_to_overlays(outputs, timestamp=job.timestamp)
        yield {
            "done": False,
            "progress": 5,
            "overlays": overlays,
            "backend": self.name,
        }

        total = self.config.max_frames
        for index, (out_frame_idx, out) in enumerate(
            model.propagate_in_video(
                inference_state,
                start_frame_idx=frame_idx,
                max_frame_num_to_track=total,
                reverse=False,
            ),
            start=1,
        ):
            timestamp = out_frame_idx * self.config.frame_interval_seconds
            overlays.extend(outputs_to_overlays(out, timestamp=timestamp))
            yield {
                "done": False,
                "progress": min(99, 5 + round((index / total) * 90)),
                "overlays": overlays,
                "backend": self.name,
            }

        yield {
            "done": True,
            "progress": 100,
            "overlays": overlays,
            "backend": self.name,
        }


def build_tracking_backend(config: TrackingBackendConfig | None = None) -> TrackingBackend:
    resolved = config or TrackingBackendConfig.from_env()
    if resolved.backend == "simulation":
        if resolved.allow_simulation_fallback:
            return SimulationTrackingBackend(
                steps=resolved.simulation_steps,
                delay_seconds=resolved.simulation_delay_seconds,
            )
        return UnavailableTrackingBackend(
            backend="simulation",
            code="simulation_fallback_disabled",
            message="Simulation tracking is disabled. Set SAM3_ALLOW_SIMULATION_FALLBACK=true for development.",
        )

    if resolved.backend not in {"sam3", "auto"}:
        return UnavailableTrackingBackend(
            backend=resolved.backend,
            code="tracking_backend_unknown",
            message=f"Unknown SAM3_TRACKING_BACKEND value: {resolved.backend}",
        )

    sam3_backend = Sam3TrackingBackend(resolved)
    if sam3_backend.status().ready or not resolved.allow_simulation_fallback:
        return sam3_backend

    return SimulationTrackingBackend(
        steps=resolved.simulation_steps,
        delay_seconds=resolved.simulation_delay_seconds,
    )


def tracking_error_payload(code: str, message: str, backend: str) -> dict[str, Any]:
    return {
        "done": True,
        "progress": 0,
        "overlays": [],
        "backend": backend,
        "error": {
            "code": code,
            "message": message,
        },
    }


def annotation_boxes_xywh(annotations: list[dict[str, Any]]) -> list[list[float]]:
    boxes: list[list[float]] = []
    for annotation in annotations:
        box = _annotation_box(annotation)
        if box is not None:
            boxes.append(box)
    return boxes


def outputs_to_overlays(outputs: Any, timestamp: float) -> list[dict[str, Any]]:
    if not isinstance(outputs, dict):
        return []

    obj_ids = _to_list(outputs.get("out_obj_ids"))
    boxes = _to_list(outputs.get("out_boxes_xywh"))
    probs = _to_list(outputs.get("out_probs"))
    overlays: list[dict[str, Any]] = []
    for index, box in enumerate(boxes):
        if not isinstance(box, list) or len(box) != 4:
            continue
        obj_id = obj_ids[index] if index < len(obj_ids) else index + 1
        score = probs[index] if index < len(probs) else None
        overlays.append(
            {
                "track_id": f"sam3-{obj_id}",
                "label": _overlay_label(obj_id, score),
                "color": DEFAULT_OVERLAY_COLOR,
                "timestamp": timestamp,
                "points": _xywh_to_points(box),
            }
        )
    return overlays


def _annotation_box(annotation: dict[str, Any]) -> list[float] | None:
    annotation_type = annotation.get("type")
    if annotation_type == "rect":
        return _box_from_values(
            annotation.get("x"),
            annotation.get("y"),
            annotation.get("width"),
            annotation.get("height"),
        )
    if annotation_type == "circle":
        x = _number(annotation.get("x"))
        y = _number(annotation.get("y"))
        radius = _number(annotation.get("radius"))
        if x is None or y is None or radius is None:
            return None
        return _box_from_values(x - radius, y - radius, radius * 2, radius * 2)
    if annotation_type == "path":
        points = annotation.get("points")
        if not isinstance(points, list) or not points:
            return None
        xs = [_number(point.get("x")) for point in points if isinstance(point, dict)]
        ys = [_number(point.get("y")) for point in points if isinstance(point, dict)]
        xs = [value for value in xs if value is not None]
        ys = [value for value in ys if value is not None]
        if not xs or not ys:
            return None
        return _box_from_values(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    return None


def _box_from_values(x: Any, y: Any, width: Any, height: Any) -> list[float] | None:
    x_value = _number(x)
    y_value = _number(y)
    width_value = _number(width)
    height_value = _number(height)
    if x_value is None or y_value is None or width_value is None or height_value is None:
        return None
    if width_value <= 0 or height_value <= 0:
        return None

    x_min = _clamp_percent(x_value)
    y_min = _clamp_percent(y_value)
    x_max = _clamp_percent(x_value + width_value)
    y_max = _clamp_percent(y_value + height_value)
    if x_max <= x_min or y_max <= y_min:
        return None
    return [x_min / 100, y_min / 100, (x_max - x_min) / 100, (y_max - y_min) / 100]


def _xywh_to_points(box: list[Any]) -> list[dict[str, float]]:
    x, y, width, height = (_clamp_unit(_number(value) or 0.0) for value in box)
    x2 = _clamp_unit(x + width)
    y2 = _clamp_unit(y + height)
    return [
        {"x": x * 100, "y": y * 100},
        {"x": x2 * 100, "y": y * 100},
        {"x": x2 * 100, "y": y2 * 100},
        {"x": x * 100, "y": y2 * 100},
    ]


def _overlay_label(obj_id: Any, score: Any) -> str:
    score_value = _number(score)
    if score_value is None:
        return f"SAM3 Track {obj_id}"
    return f"SAM3 Track {obj_id} ({score_value:.2f})"


def _simulated_overlay(t: float, offset: float) -> list[dict[str, Any]]:
    x = 20 + (offset * 1.7)
    return [
        {
            "track_id": "simulated-lever-1",
            "label": "Simulated Tracking Overlay",
            "color": DEFAULT_OVERLAY_COLOR,
            "timestamp": t,
            "points": [
                {"x": x, "y": 35},
                {"x": x + 18, "y": 35},
                {"x": x + 18, "y": 62},
                {"x": x, "y": 62},
            ],
        }
    ]


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_percent(value: float) -> float:
    return min(100.0, max(0.0, value))


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
