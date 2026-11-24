"""Haar and YuNet detectors, returning face boxes in camera coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

import config


@dataclass
class Face:
    """One detected face in full camera-image coordinates."""

    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> int:
        return self.width * self.height


def _find_haar_file() -> Path:
    """Support both Raspberry Pi OS and pip OpenCV installations."""

    candidates = [
        Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
        Path("/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml"),
    ]
    cv_data = getattr(cv2, "data", None)
    if cv_data is not None and hasattr(cv_data, "haarcascades"):
        candidates.insert(
            0, Path(cv_data.haarcascades) / "haarcascade_frontalface_default.xml"
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Haar cascade file not found. Install opencv-data.")


class HaarDetector:
    """Fast classical detector supplied with OpenCV."""

    def __init__(self) -> None:
        self.model = cv2.CascadeClassifier(str(_find_haar_file()))
        if self.model.empty():
            raise RuntimeError("OpenCV could not load the Haar cascade.")

    def detect(self, colour_frame: np.ndarray) -> list[Face]:
        small = cv2.resize(
            colour_frame, (config.DETECTION_WIDTH, config.DETECTION_HEIGHT)
        )
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        boxes = self.model.detectMultiScale(
            grey,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(25, 25),
        )

        scale_x = config.CAMERA_WIDTH / config.DETECTION_WIDTH
        scale_y = config.CAMERA_HEIGHT / config.DETECTION_HEIGHT
        return [
            Face(
                x=round(x * scale_x),
                y=round(y * scale_y),
                width=round(width * scale_x),
                height=round(height * scale_y),
                confidence=1.0,
            )
            for x, y, width, height in boxes
        ]


class YuNetDetector:
    """Small CNN face detector from the official OpenCV Model Zoo."""

    def __init__(self) -> None:
        if not config.YUNET_MODEL.is_file():
            raise FileNotFoundError(f"YuNet model not found: {config.YUNET_MODEL}")

        input_size = (config.DETECTION_WIDTH, config.DETECTION_HEIGHT)
        self.model = cv2.FaceDetectorYN.create(
            model=str(config.YUNET_MODEL),
            config="",
            input_size=input_size,
            score_threshold=0.70,
            nms_threshold=0.30,
            top_k=5000,
        )

    def detect(self, colour_frame: np.ndarray) -> list[Face]:
        small = cv2.resize(
            colour_frame, (config.DETECTION_WIDTH, config.DETECTION_HEIGHT)
        )
        _, rows = self.model.detect(small)
        if rows is None:
            return []

        scale_x = config.CAMERA_WIDTH / config.DETECTION_WIDTH
        scale_y = config.CAMERA_HEIGHT / config.DETECTION_HEIGHT
        return [
            Face(
                x=round(float(row[0]) * scale_x),
                y=round(float(row[1]) * scale_y),
                width=round(float(row[2]) * scale_x),
                height=round(float(row[3]) * scale_y),
                confidence=float(row[-1]),
            )
            for row in rows
        ]
