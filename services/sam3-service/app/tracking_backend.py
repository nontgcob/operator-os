from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Protocol

try:
    from services.common.env import load_env_file
except ImportError:
    load_env_file = None

if load_env_file:
    load_env_file()

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
    rendered_video_root: Path = Path("data/tracking")
    device: str | None = None
    max_frames: int = 0
    frame_interval_seconds: float = 1.0
    simulation_steps: int = 10
    simulation_delay_seconds: float = 0.35
    max_polygon_points: int = 512
    image_size: int = 1024

    @classmethod
    def from_env(cls) -> "TrackingBackendConfig":
        checkpoint = os.getenv("SAM3_CHECKPOINT_PATH", "./models/sam3.pt")
        return cls(
            backend=os.getenv("SAM3_TRACKING_BACKEND", "sam3").strip().lower(),
            allow_simulation_fallback=_env_flag("SAM3_ALLOW_SIMULATION_FALLBACK"),
            checkpoint_path=Path(checkpoint).expanduser() if checkpoint else None,
            allow_hf_download=_env_flag("SAM3_ALLOW_HF_DOWNLOAD"),
            video_root=Path(os.getenv("SAM3_VIDEO_ROOT", "data/video")).expanduser(),
            rendered_video_root=Path(os.getenv("SAM3_RENDERED_VIDEO_ROOT", "data/tracking")).expanduser(),
            device=os.getenv("SAM3_DEVICE") or None,
            max_frames=max(0, _env_int("SAM3_MAX_PROPAGATION_FRAMES", 0)),
            frame_interval_seconds=max(0.001, _env_float("SAM3_FRAME_INTERVAL_SECONDS", 1.0)),
            simulation_steps=max(1, _env_int("SAM3_SIMULATION_STEPS", 10)),
            simulation_delay_seconds=max(0.0, _env_float("SAM3_SIMULATION_DELAY_SECONDS", 0.35)),
            max_polygon_points=max(8, _env_int("SAM3_MAX_POLYGON_POINTS", 512)),
            image_size=max(320, _env_int("SAM3_IMAGE_SIZE", 1024)),
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
        self._predictors: dict[str, Any] = {}
        self._model_lock = asyncio.Lock()

    def status(self) -> TrackingBackendStatus:
        if importlib.util.find_spec("ultralytics") is None:
            return TrackingBackendStatus(
                backend=self.name,
                ready=False,
                code="sam3_dependency_missing",
                message="Python package 'ultralytics' is not installed in this environment.",
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
                    "the working directory model path to a local Ultralytics-compatible sam3.pt."
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

    async def _load_predictor(self, predictor_type: str) -> Any:
        async with self._model_lock:
            if predictor_type in self._predictors:
                return self._predictors[predictor_type]

            def load_model() -> Any:
                import torch
                from ultralytics.models.sam import SAM3VideoPredictor, SAM3VideoSemanticPredictor

                model_path = str(self.config.checkpoint_path) if self.config.checkpoint_path else "sam3.pt"
                os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
                overrides: dict[str, Any] = {
                    "conf": 0.50,
                    "show_conf": True,
                    "task": "segment",
                    "mode": "predict",
                    "model": model_path,
                    "imgsz": self.config.image_size,
                    "compile": False,
                    "half": bool(torch.cuda.is_available() and self.config.device != "cpu"),
                    "save": False,
                    "verbose": False,
                }
                if self.config.device:
                    overrides["device"] = self.config.device
                predictor_cls = SAM3VideoPredictor if predictor_type == "boxes" else SAM3VideoSemanticPredictor
                return predictor_cls(overrides=overrides)

            self._predictors[predictor_type] = await asyncio.to_thread(load_model)
            return self._predictors[predictor_type]

    async def _track_in_worker(self, job: TrackingJob, video_path: Path) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        def put(item: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        async def load_model() -> Any:
            try:
                predictor_type = "boxes" if annotation_boxes_xywh(job.annotations) else "text"
                return await self._load_predictor(predictor_type)
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
        predictor: Any,
        job: TrackingJob,
        video_path: Path,
    ) -> Iterator[dict[str, Any]]:
        clip_path, frame_width, frame_height, clip_fps, clip_frame_count = self._clip_from_timestamp(video_path, job)
        boxes = annotation_boxes_xywh(job.annotations)
        prompt = job.segmentation_prompt or job.question
        rendered_path = self.config.rendered_video_root / f"{job.tracking_job_id}.mp4"
        working_rendered_path = rendered_path.with_suffix(".working.mp4")
        rendered_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_writer: Any = None

        if boxes:
            pixel_boxes = [_xywh_unit_to_xyxy_pixels(box, frame_width, frame_height) for box in boxes]
            results = predictor(source=str(clip_path), bboxes=pixel_boxes, stream=True)
        else:
            results = predictor(source=str(clip_path), text=[prompt], stream=True)

        total = max(1, clip_frame_count)
        try:
            for index, result in enumerate(results, start=1):
                timestamp = job.timestamp + ((index - 1) / clip_fps)
                rendered_frame = _render_result_mask(result)
                if rendered_frame is not None:
                    if rendered_writer is None:
                        import cv2

                        frame_height, frame_width = rendered_frame.shape[:2]
                        rendered_writer = cv2.VideoWriter(
                            str(working_rendered_path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            clip_fps,
                            (frame_width, frame_height),
                        )
                        if not rendered_writer.isOpened():
                            raise RuntimeError(f"Unable to create rendered SAM3 video: {working_rendered_path}")
                    rendered_writer.write(rendered_frame)
                yield {
                    "done": False,
                    "progress": min(99, round((index / total) * 100)),
                    "overlays": [],
                    "backend": self.name,
                }
                if self.config.max_frames > 0 and index >= self.config.max_frames:
                    break
        finally:
            if rendered_writer is not None:
                rendered_writer.release()

        if rendered_writer is None or not working_rendered_path.exists():
            raise RuntimeError("SAM3 completed without producing a rendered tracking video.")
        _transcode_rendered_video(working_rendered_path, rendered_path)

        yield {
            "done": True,
            "progress": 100,
            "overlays": [],
            "backend": self.name,
            "rendered_video_path": str(rendered_path),
        }

    def _clip_from_timestamp(self, video_path: Path, job: TrackingJob) -> tuple[Path, int, int, float, int]:
        import cv2

        output_path = Path(tempfile.gettempdir()) / f"operatoros-sam3-{job.tracking_job_id}.mp4"
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            return self._clip_from_extracted_frames(video_path, job, output_path)

        fps = capture.get(cv2.CAP_PROP_FPS) or (1.0 / self.config.frame_interval_seconds)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        start_frame = max(0, round(job.timestamp * fps))
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            capture.release()
            raise RuntimeError("Unable to create temporary SAM3 tracking clip.")

        written = 0
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            while self.config.max_frames == 0 or written < self.config.max_frames:
                ok, frame = capture.read()
                if not ok:
                    break
                writer.write(frame)
                written += 1
        finally:
            writer.release()
            capture.release()

        if written == 0:
            return self._clip_from_extracted_frames(video_path, job, output_path)

        return output_path, width, height, fps, written

    def _clip_from_extracted_frames(
        self,
        video_path: Path,
        job: TrackingJob,
        output_path: Path,
    ) -> tuple[Path, int, int, float, int]:
        import cv2

        frame_dir = video_path.parent / "frames"
        frame_paths = sorted(frame_dir.glob("frame_*.jpg"))
        if not frame_paths:
            raise RuntimeError(
                "Unable to decode video for SAM3 tracking and no extracted JPEG frames were found."
            )

        start_index = max(0, round(job.timestamp / self.config.frame_interval_seconds))
        selected = (
            frame_paths[start_index : start_index + self.config.max_frames]
            if self.config.max_frames > 0
            else frame_paths[start_index:]
        )
        if not selected:
            raise RuntimeError("No extracted frames are available at the requested SAM3 timestamp.")

        first = cv2.imread(str(selected[0]))
        if first is None:
            raise RuntimeError(f"Unable to read extracted frame for SAM3 tracking: {selected[0]}")
        height, width = first.shape[:2]
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            1.0 / self.config.frame_interval_seconds,
            (width, height),
        )
        if not writer.isOpened():
            raise RuntimeError("Unable to create temporary SAM3 tracking clip from extracted frames.")

        try:
            writer.write(first)
            for frame_path in selected[1:]:
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    continue
                writer.write(frame)
        finally:
            writer.release()

        return output_path, width, height, 1.0 / self.config.frame_interval_seconds, len(selected)


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


def ultralytics_result_to_overlays(
    result: Any,
    timestamp: float,
    max_polygon_points: int = 160,
) -> list[dict[str, Any]]:
    orig_shape = getattr(result, "orig_shape", None)
    if not isinstance(orig_shape, tuple) or len(orig_shape) < 2:
        return []
    height, width = float(orig_shape[0]), float(orig_shape[1])
    if width <= 0 or height <= 0:
        return []

    overlays: list[dict[str, Any]] = []
    boxes = getattr(result, "boxes", None)
    confs = _to_list(getattr(boxes, "conf", None))
    ids = _to_list(getattr(boxes, "id", None))
    masks = getattr(result, "masks", None)
    mask_data = _to_numpy(getattr(masks, "data", None))
    if mask_data is not None and getattr(mask_data, "ndim", 0) == 3:
        for mask_index, mask in enumerate(mask_data):
            obj_id = ids[mask_index] if mask_index < len(ids) else mask_index + 1
            score = confs[mask_index] if mask_index < len(confs) else None
            for contour_index, points in enumerate(
                _mask_contours_to_percent_points(mask, int(width), int(height), max_polygon_points)
            ):
                overlays.append(
                    {
                        "track_id": f"sam3-{obj_id}-contour-{contour_index + 1}",
                        "label": _overlay_label(obj_id, score),
                        "color": DEFAULT_OVERLAY_COLOR,
                        "timestamp": timestamp,
                        "points": points,
                    }
                )
        if overlays:
            return overlays

    xyxy = _to_list(getattr(boxes, "xyxy", None))
    for index, box in enumerate(xyxy):
        if not isinstance(box, list) or len(box) < 4:
            continue
        obj_id = ids[index] if index < len(ids) else index + 1
        score = confs[index] if index < len(confs) else None
        overlays.append(
            {
                "track_id": f"sam3-{obj_id}",
                "label": _overlay_label(obj_id, score),
                "color": DEFAULT_OVERLAY_COLOR,
                "timestamp": timestamp,
                "points": _xyxy_pixels_to_points(box, width, height),
            }
        )
    return overlays


def _mask_contours_to_percent_points(
    mask: Any,
    width: int,
    height: int,
    max_points: int,
) -> list[list[dict[str, float]]]:
    import cv2
    import numpy as np

    binary = (np.asarray(mask) > 0.5).astype(np.uint8)
    if binary.shape[:2] != (height, width):
        binary = cv2.resize(binary, (width, height), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    minimum_area = max(4.0, width * height * 0.00005)
    contours = [contour for contour in contours if cv2.contourArea(contour) >= minimum_area]
    contours.sort(key=cv2.contourArea, reverse=True)

    result: list[list[dict[str, float]]] = []
    for contour in contours:
        raw_points = contour.reshape(-1, 2).tolist()
        points = [
            {
                "x": _clamp_percent((float(x) / width) * 100),
                "y": _clamp_percent((float(y) / height) * 100),
            }
            for x, y in raw_points
        ]
        points = _downsample_points(points, max_points)
        if len(points) >= 3:
            result.append(points)
    return result


def _render_result_mask(result: Any) -> Any:
    import cv2
    import numpy as np

    frame = getattr(result, "orig_img", None)
    if frame is None:
        return None
    annotated = np.asarray(frame).copy()
    masks = getattr(result, "masks", None)
    mask_data = _to_numpy(getattr(masks, "data", None))
    if mask_data is None or getattr(mask_data, "ndim", 0) != 3:
        return annotated

    color = np.asarray((82, 165, 103), dtype=np.float32)
    for mask in mask_data:
        binary = np.asarray(mask) > 0.5
        if binary.shape[:2] != annotated.shape[:2]:
            binary = cv2.resize(
                binary.astype(np.uint8),
                (annotated.shape[1], annotated.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
        annotated[binary] = (0.55 * annotated[binary] + 0.45 * color).astype(np.uint8)
    return annotated


def _transcode_rendered_video(input_path: Path, output_path: Path) -> None:
    ffmpeg = os.getenv("FFMPEG_BINARY", "ffmpeg")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.is_file():
        message = completed.stderr.strip() or "FFmpeg did not create an output file."
        raise RuntimeError(f"Unable to encode browser-compatible SAM3 video: {message}")
    input_path.unlink(missing_ok=True)


def _downsample_points(
    points: list[dict[str, float]],
    max_points: int,
) -> list[dict[str, float]]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[min(len(points) - 1, round(index * step))] for index in range(max_points)]


def _xywh_unit_to_xyxy_pixels(box: list[float], width: int, height: int) -> list[float]:
    x, y, box_width, box_height = box
    return [
        _clamp_unit(x) * width,
        _clamp_unit(y) * height,
        _clamp_unit(x + box_width) * width,
        _clamp_unit(y + box_height) * height,
    ]


def _xyxy_pixels_to_points(box: list[Any], width: float, height: float) -> list[dict[str, float]]:
    x1, y1, x2, y2 = (_number(value) or 0.0 for value in box[:4])
    return [
        {"x": _clamp_percent((x1 / width) * 100), "y": _clamp_percent((y1 / height) * 100)},
        {"x": _clamp_percent((x2 / width) * 100), "y": _clamp_percent((y1 / height) * 100)},
        {"x": _clamp_percent((x2 / width) * 100), "y": _clamp_percent((y2 / height) * 100)},
        {"x": _clamp_percent((x1 / width) * 100), "y": _clamp_percent((y2 / height) * 100)},
    ]


def _polygon_to_percent_points(polygon: Any, width: float, height: float) -> list[dict[str, float]]:
    raw_points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
    if not isinstance(raw_points, list):
        return []
    points: list[dict[str, float]] = []
    for point in raw_points:
        if not isinstance(point, list) or len(point) < 2:
            continue
        x = _number(point[0])
        y = _number(point[1])
        if x is None or y is None:
            continue
        points.append(
            {
                "x": _clamp_percent((x / width) * 100),
                "y": _clamp_percent((y / height) * 100),
            }
        )
    return points


def _annotation_box(annotation: dict[str, Any]) -> list[float] | None:
    annotation_type = annotation.get("type")
    coordinate_scale = _annotation_coordinate_scale(annotation)
    if annotation_type == "rect":
        return _box_from_values(
            annotation.get("x"),
            annotation.get("y"),
            annotation.get("width"),
            annotation.get("height"),
            coordinate_scale=coordinate_scale,
        )
    if annotation_type == "circle":
        x = _number(annotation.get("cx", annotation.get("x")))
        y = _number(annotation.get("cy", annotation.get("y")))
        radius = _number(annotation.get("r", annotation.get("radius")))
        if x is None or y is None or radius is None:
            return None
        return _box_from_values(
            x - radius,
            y - radius,
            radius * 2,
            radius * 2,
            coordinate_scale=coordinate_scale,
        )
    if annotation_type in {"path", "polygon"}:
        points = annotation.get("points")
        xs, ys = _points_xy(points)
        if annotation_type == "path" and not xs and isinstance(annotation.get("d"), str):
            xs, ys = _path_xy(annotation["d"])
        if not xs or not ys:
            return None
        return _box_from_values(
            min(xs),
            min(ys),
            max(xs) - min(xs),
            max(ys) - min(ys),
            coordinate_scale=coordinate_scale,
        )
    if annotation_type == "arrow":
        x1 = _number(annotation.get("x1"))
        y1 = _number(annotation.get("y1"))
        x2 = _number(annotation.get("x2"))
        y2 = _number(annotation.get("y2"))
        if x1 is None or y1 is None or x2 is None or y2 is None:
            return None
        return _box_from_values(
            min(x1, x2),
            min(y1, y2),
            abs(x2 - x1),
            abs(y2 - y1),
            coordinate_scale=coordinate_scale,
        )
    return None


def _box_from_values(
    x: Any,
    y: Any,
    width: Any,
    height: Any,
    *,
    coordinate_scale: float = 100.0,
) -> list[float] | None:
    x_value = _number(x)
    y_value = _number(y)
    width_value = _number(width)
    height_value = _number(height)
    if x_value is None or y_value is None or width_value is None or height_value is None:
        return None
    if width_value <= 0 or height_value <= 0:
        return None

    x_min = _clamp_percent((x_value / coordinate_scale) * 100)
    y_min = _clamp_percent((y_value / coordinate_scale) * 100)
    x_max = _clamp_percent(((x_value + width_value) / coordinate_scale) * 100)
    y_max = _clamp_percent(((y_value + height_value) / coordinate_scale) * 100)
    if x_max <= x_min or y_max <= y_min:
        return None
    return [x_min / 100, y_min / 100, (x_max - x_min) / 100, (y_max - y_min) / 100]


def _annotation_coordinate_scale(annotation: dict[str, Any]) -> float:
    if annotation.get("coordinate_space") == "ragvlm_0_1000":
        return 1000.0
    values: list[float] = []
    for key in ("x", "y", "cx", "cy", "r", "radius", "x1", "y1", "x2", "y2", "width", "height"):
        value = _number(annotation.get(key))
        if value is not None:
            values.append(value)
    xs, ys = _points_xy(annotation.get("points"))
    values.extend(xs)
    values.extend(ys)
    return 1000.0 if values and max(values) > 100 else 100.0


def _points_xy(points: Any) -> tuple[list[float], list[float]]:
    if not isinstance(points, list):
        return [], []
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if isinstance(point, dict):
            x = _number(point.get("x"))
            y = _number(point.get("y"))
        elif isinstance(point, list) and len(point) >= 2:
            x = _number(point[0])
            y = _number(point[1])
        else:
            continue
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    return xs, ys


def _path_xy(path: str) -> tuple[list[float], list[float]]:
    numbers = [_number(match) for match in re.findall(r"[-+]?\d*\.?\d+", path)]
    values = [number for number in numbers if number is not None]
    return values[0::2], values[1::2]


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


def _to_numpy(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    try:
        import numpy as np

        return np.asarray(value)
    except (TypeError, ValueError):
        return None


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
