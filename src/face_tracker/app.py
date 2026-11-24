"""Camera, face tracking, and the web interface. Run with python3 app.py."""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import time
from http import HTTPStatus, server
from threading import Condition, Event, Lock, Thread
from typing import Any

import cv2
from picamera2 import Picamera2

import config
from detectors import Face, HaarDetector, YuNetDetector
from servos import PanTiltServos, clamp


class RuntimeState:
    """Thread-safe settings, commands, and measurements shared with the web UI."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.settings: dict[str, Any] = {
            "detector": config.DEFAULT_DETECTOR,
            "speed_dps": config.DEFAULT_SPEED_DPS,
            "reverse_pan": config.DEFAULT_REVERSE_PAN,
            "reverse_tilt": config.DEFAULT_REVERSE_TILT,
            "tracking": False,
        }
        self.status: dict[str, Any] = {
            "running": True,
            "faces": 0,
            "confidence": 0.0,
            "fps": 0.0,
            "detection_ms": 0.0,
            "loop_ms": 0.0,
            "error_x": 0.0,
            "error_y": 0.0,
            "pan": config.PAN_HOME_DEGREES,
            "tilt": config.TILT_HOME_DEGREES,
            "pan_rate_dps": 0.0,
            "tilt_rate_dps": 0.0,
            "controller_state": "idle",
            "servo_available": False,
            "servo_outputs": False,
            "servo_error": None,
            "application_error": None,
        }
        self.home_requested = False
        self.nudge_requested: tuple[str, float] | None = None

    def web_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                **self.settings,
                **self.status,
                "home_requested": self.home_requested,
            }

    def detector_name(self) -> str:
        with self.lock:
            return self.settings["detector"]

    def update_status(self, **values: Any) -> None:
        with self.lock:
            self.status.update(values)

    def change_settings(self, values: dict[str, Any]) -> None:
        with self.lock:
            settings = self.settings.copy()
            if "detector" in values:
                detector = str(values["detector"])
                if detector not in ("haar", "yunet"):
                    raise ValueError("detector must be 'haar' or 'yunet'")
                settings["detector"] = detector

            if "speed_dps" in values:
                speed = float(values["speed_dps"])
                if not config.MIN_SPEED_DPS <= speed <= config.MAX_SPEED_DPS:
                    raise ValueError(
                        f"speed_dps must be {config.MIN_SPEED_DPS:g} to "
                        f"{config.MAX_SPEED_DPS:g}"
                    )
                settings["speed_dps"] = speed

            for name in ("reverse_pan", "reverse_tilt", "tracking"):
                if name in values:
                    if not isinstance(values[name], bool):
                        raise ValueError(f"{name} must be true or false")
                    settings[name] = values[name]

            # Apply only after the whole request has passed validation.
            self.settings = settings
            if "tracking" in values:
                self.home_requested = not values["tracking"]
                self.nudge_requested = None

    def request_home(self) -> None:
        with self.lock:
            self.settings["tracking"] = False
            self.home_requested = True
            self.nudge_requested = None

    def finish_home(self) -> None:
        with self.lock:
            self.home_requested = False

    def request_nudge(self, axis: str, degrees: float) -> None:
        if axis not in ("pan", "tilt"):
            raise ValueError("axis must be 'pan' or 'tilt'")
        if degrees not in (-5.0, 5.0):
            raise ValueError("manual nudge must be -5 or +5 degrees")
        with self.lock:
            self.settings["tracking"] = False
            self.home_requested = False
            self.nudge_requested = (axis, degrees)

    def take_motion(self) -> tuple[dict[str, Any], bool, tuple[str, float] | None]:
        # Read related controls together at the point where the loop can move.
        with self.lock:
            nudge = self.nudge_requested
            self.nudge_requested = None
            return self.settings.copy(), self.home_requested, nudge


class LatestFrame:
    """Keep only the newest JPEG so the browser never builds a frame backlog."""

    def __init__(self) -> None:
        self.condition = Condition()
        self.jpeg: bytes | None = None
        self.number = 0

    def publish(self, jpeg: bytes) -> None:
        with self.condition:
            self.jpeg = jpeg
            self.number += 1
            self.condition.notify_all()

    def wait(self, previous_number: int) -> tuple[int, bytes | None]:
        with self.condition:
            self.condition.wait_for(
                lambda: self.number != previous_number,
                timeout=2.0,
            )
            return self.number, self.jpeg


class TrackerLoop:
    """Camera, face detection, overlay, and servo correction loop."""

    def __init__(
        self,
        state: RuntimeState,
        latest_frame: LatestFrame,
        servo_hardware_enabled: bool,
    ) -> None:
        self.state = state
        self.latest_frame = latest_frame
        self.stop_event = Event()
        self.servos = PanTiltServos(hardware_enabled=servo_hardware_enabled)
        self.camera: Picamera2 | None = None
        self.thread = Thread(target=self.run, daemon=True, name="tracker-loop")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)
        self.servos.close()
        if self.camera is not None:
            try:
                self.camera.stop()
                self.camera.close()
            except Exception:
                logging.exception("Camera shutdown failed")

    @staticmethod
    def draw_overlay(
        frame: Any,
        face: Face | None,
        filtered_centre: tuple[float, float] | None,
        settings: dict[str, Any],
        error_x: float,
        error_y: float,
        servos: PanTiltServos,
    ) -> None:
        centre_x = config.CAMERA_WIDTH // 2
        centre_y = config.CAMERA_HEIGHT // 2
        half_deadband_width = round(config.DEADBAND_X * config.CAMERA_WIDTH / 2)
        half_deadband_height = round(config.DEADBAND_Y * config.CAMERA_HEIGHT / 2)

        # White cross and rectangle: the target point and tolerated deadband.
        cv2.drawMarker(
            frame,
            (centre_x, centre_y),
            (255, 255, 255),
            cv2.MARKER_CROSS,
            24,
            2,
        )
        cv2.rectangle(
            frame,
            (
                centre_x - half_deadband_width,
                centre_y - half_deadband_height,
            ),
            (
                centre_x + half_deadband_width,
                centre_y + half_deadband_height,
            ),
            (255, 255, 255),
            1,
        )

        if face is not None:
            cv2.rectangle(
                frame,
                (face.x, face.y),
                (face.x + face.width, face.y + face.height),
                (0, 220, 0),
                2,
            )
            face_centre = tuple(round(value) for value in face.centre)
            cv2.circle(frame, face_centre, 5, (0, 255, 255), -1)
            cv2.line(
                frame,
                (centre_x, centre_y),
                face_centre,
                (0, 180, 255),
                1,
            )
            cv2.putText(
                frame,
                f"{settings['detector']} {face.confidence:.2f}",
                (face.x, max(18, face.y - 7)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (0, 220, 0),
                1,
                cv2.LINE_AA,
            )

        if filtered_centre is not None:
            cv2.circle(
                frame,
                tuple(round(value) for value in filtered_centre),
                9,
                (255, 0, 255),
                2,
            )

        mode = "TRACKING" if settings["tracking"] else "OBSERVING"
        lines = [
            f"{mode} | detector={settings['detector']} | max={settings['speed_dps']:.0f} deg/s",
            f"error x={error_x:+.3f}, y={error_y:+.3f}",
            f"commanded pan={servos.pan:.1f}, tilt={servos.tilt:.1f} deg",
        ]
        for row, text in enumerate(lines):
            cv2.putText(
                frame,
                text,
                (10, 22 + row * 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (20, 20, 20),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                text,
                (10, 22 + row * 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (245, 245, 245),
                1,
                cv2.LINE_AA,
            )

    def run(self) -> None:
        try:
            cv2.setNumThreads(2)
            detectors = {"haar": HaarDetector(), "yunet": YuNetDetector()}
            self.servos.connect()
            self.state.update_status(
                servo_available=self.servos.available,
                servo_error=self.servos.error,
            )

            self.camera = Picamera2()
            self.camera.configure(
                self.camera.create_video_configuration(
                    main={
                        "size": (config.CAMERA_WIDTH, config.CAMERA_HEIGHT),
                        "format": "RGB888",
                    },
                    controls={"FrameRate": config.CAMERA_FPS},
                )
            )
            self.camera.start()

            filtered_centre: tuple[float, float] | None = None
            last_face_time = time.monotonic()
            face_missing_since: float | None = None
            previous_tracking = False
            previous_loop_time = time.monotonic()
            fps_started = previous_loop_time
            fps_frames = 0
            fps = 0.0

            while not self.stop_event.is_set():
                loop_started = time.monotonic()
                # A delayed frame must not cause a large motor step.
                elapsed = clamp(loop_started - previous_loop_time, 0.001, 0.1)
                previous_loop_time = loop_started
                detector_name = self.state.detector_name()

                colour_frame = self.camera.capture_array("main")

                detection_started = time.monotonic()
                faces = detectors[detector_name].detect(colour_frame)
                detection_ms = (time.monotonic() - detection_started) * 1000
                face = max(faces, key=lambda face: face.area, default=None)
                if self.stop_event.is_set():
                    break

                settings, home_requested, nudge = self.state.take_motion()
                settings["detector"] = detector_name

                if settings["tracking"] and not previous_tracking:
                    # Discard the previous target when tracking restarts.
                    filtered_centre = None
                    last_face_time = loop_started
                previous_tracking = settings["tracking"]

                error_x = 0.0
                error_y = 0.0
                confidence = 0.0
                if face is not None:
                    face_missing_since = None
                    confidence = face.confidence
                    raw_x, raw_y = face.centre
                    if filtered_centre is None:
                        filtered_centre = (raw_x, raw_y)
                    else:
                        alpha = config.FACE_SMOOTHING
                        filtered_centre = (
                            alpha * raw_x + (1 - alpha) * filtered_centre[0],
                            alpha * raw_y + (1 - alpha) * filtered_centre[1],
                        )

                    error_x = (
                        filtered_centre[0] - config.CAMERA_WIDTH / 2
                    ) / (config.CAMERA_WIDTH / 2)
                    error_y = (
                        filtered_centre[1] - config.CAMERA_HEIGHT / 2
                    ) / (config.CAMERA_HEIGHT / 2)
                    last_face_time = loop_started
                elif face_missing_since is None:
                    face_missing_since = loop_started
                elif loop_started - face_missing_since > config.FILTER_RESET_SECONDS:
                    filtered_centre = None

                controller_state = "idle"
                if nudge is not None:
                    self.servos.nudge(*nudge)
                    controller_state = "manual move"
                elif home_requested:
                    controller_state = "returning home"
                    at_home = self.servos.step_home(
                        elapsed,
                        settings["speed_dps"],
                    )
                    if at_home:
                        disabled = self.servos.disable_outputs()
                        self.state.finish_home()
                        controller_state = "home; PWM off" if disabled else "home; PWM off failed"
                elif settings["tracking"] and face is not None:
                    controller_state = "correcting face error"
                    # First tracking write establishes the known commanded home.
                    if not self.servos.outputs_active:
                        self.servos.write()
                    self.servos.track(
                        error_x=error_x,
                        error_y=error_y,
                        elapsed_seconds=elapsed,
                        speed_dps=settings["speed_dps"],
                        reverse_pan=settings["reverse_pan"],
                        reverse_tilt=settings["reverse_tilt"],
                    )
                elif (
                    settings["tracking"]
                    and loop_started - last_face_time
                    > config.LOST_FACE_RETURN_HOME_SECONDS
                ):
                    self.state.request_home()
                    controller_state = "face lost; returning home"
                elif settings["tracking"]:
                    controller_state = "waiting for face"

                self.draw_overlay(
                    colour_frame,
                    face,
                    filtered_centre if face is not None else None,
                    settings,
                    error_x,
                    error_y,
                    self.servos,
                )
                encoded, jpeg = cv2.imencode(
                    ".jpg",
                    colour_frame,
                    [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY],
                )
                if encoded:
                    self.latest_frame.publish(jpeg.tobytes())

                fps_frames += 1
                if loop_started - fps_started >= 1.0:
                    fps = fps_frames / (loop_started - fps_started)
                    fps_started = loop_started
                    fps_frames = 0

                self.state.update_status(
                    running=True,
                    faces=len(faces),
                    confidence=confidence,
                    fps=fps,
                    detection_ms=detection_ms,
                    loop_ms=(time.monotonic() - loop_started) * 1000,
                    error_x=error_x,
                    error_y=error_y,
                    pan=self.servos.pan,
                    tilt=self.servos.tilt,
                    pan_rate_dps=self.servos.pan_rate_dps,
                    tilt_rate_dps=self.servos.tilt_rate_dps,
                    controller_state=controller_state,
                    servo_available=self.servos.available,
                    servo_outputs=self.servos.outputs_active,
                    servo_error=self.servos.error,
                )

        except Exception as error:
            logging.exception("Tracker loop failed")
            self.state.update_status(
                running=False,
                application_error=str(error),
            )
        finally:
            if not self.servos.disable_outputs():
                logging.error("%s", self.servos.error)
                self.state.update_status(servo_error=self.servos.error)


class WebHandler(server.BaseHTTPRequestHandler):
    """JSON controls and MJPEG stream for static/index.html."""

    state: RuntimeState
    latest_frame: LatestFrame
    index_html: bytes
    timeout = 5

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def local_host(self) -> str | None:
        # Allow loopback names and the laptop's forwarded port, never other names.
        hosts = self.headers.get_all("Host", [])
        if len(hosts) == 1 and re.fullmatch(
            r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", hosts[0]
        ):
            return hosts[0]
        return None

    def send_json(
        self,
        value: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Expected one Content-Length header")
        length = int(lengths[0])
        if not 1 <= length <= 4096:
            raise ValueError("JSON body must be between 1 and 4096 bytes")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete JSON body")
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        if self.local_host() is None:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self.index_html)))
            self.end_headers()
            self.wfile.write(self.index_html)
            return
        if path == "/api/status":
            self.send_json(self.state.web_snapshot())
            return
        if path != "/stream.mjpg":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=FRAME",
        )
        self.end_headers()
        frame_number = -1
        try:
            while True:
                frame_number, jpeg = self.latest_frame.wait(frame_number)
                if jpeg is None:
                    continue
                self.wfile.write(b"--FRAME\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(
                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except OSError:
            pass

    def do_POST(self) -> None:  # noqa: N802
        host = self.local_host()
        origins = self.headers.get_all("Origin", [])
        if host is None or (origins and origins != [f"http://{host}"]):
            self.send_json({"error": "Use the local tracker page"}, HTTPStatus.FORBIDDEN)
            return
        # Require the JSON format used by our own page for every command.
        if self.headers.get_content_type() != "application/json":
            self.send_json({"error": "Send application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return

        path = self.path.split("?", 1)[0]
        try:
            body = self.read_json()
            if path == "/api/settings":
                self.state.change_settings(body)
            elif path == "/api/home":
                self.state.request_home()
            elif path == "/api/nudge":
                self.state.request_nudge(
                    str(body.get("axis")),
                    float(body.get("degrees")),
                )
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True})
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            self.send_json(
                {"ok": False, "error": str(error)},
                HTTPStatus.BAD_REQUEST,
            )

    def log_message(self, format: str, *args: object) -> None:
        logging.debug(format, *args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-servo",
        action="store_true",
        help="run camera, detectors and web UI without PCA9685 writes",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.WEB_PORT,
        help=f"web port (default: {config.WEB_PORT})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    index_html = config.STATIC_FILE.read_bytes()

    state = RuntimeState()
    latest_frame = LatestFrame()
    tracker = TrackerLoop(
        state,
        latest_frame,
        servo_hardware_enabled=not args.no_servo,
    )

    WebHandler.state = state
    WebHandler.latest_frame = latest_frame
    WebHandler.index_html = index_html
    web_server = server.ThreadingHTTPServer(
        (config.WEB_HOST, args.port),
        WebHandler,
    )
    web_server.daemon_threads = True

    def stop_signal(_signal_number: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    tracker.start()
    print(f"Open http://127.0.0.1:{args.port} on the Pi or use an SSH tunnel.", flush=True)
    print("Tracking starts OFF. Use the web page to enable it.", flush=True)
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping camera and disabling servo outputs.")
    finally:
        try:
            tracker.stop()
        finally:
            web_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
