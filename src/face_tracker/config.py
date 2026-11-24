"""Camera settings and measured hardware calibration."""

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
STATIC_FILE = PROJECT_DIR / "static" / "index.html"
YUNET_MODEL = PROJECT_DIR / "models" / "face_detection_yunet_2023mar.onnx"

# The camera and motor controls are reached through an SSH tunnel.
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080
DEFAULT_DETECTOR = "yunet"

# The camera stream stays in colour. Detection runs on a smaller copy.
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 20
DETECTION_WIDTH = 320
DETECTION_HEIGHT = 240
JPEG_QUALITY = 85

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
PWM_FREQUENCY_HZ = 50

PAN_CHANNEL = 0
TILT_CHANNEL = 15
PAN_MIN_DEGREES = 0.0
PAN_MAX_DEGREES = 180.0
TILT_MIN_DEGREES = 0.0
TILT_MAX_DEGREES = 180.0
PAN_HOME_DEGREES = 90.0
TILT_HOME_DEGREES = 90.0

# These pulse widths match the servo test that already worked.
SERVO_MIN_PULSE_US = 1000
SERVO_MAX_PULSE_US = 2000

DEFAULT_SPEED_DPS = 120.0
MIN_SPEED_DPS = 5.0
MAX_SPEED_DPS = 240.0

# Both directions were verified on the assembled bracket.
DEFAULT_REVERSE_PAN = True
DEFAULT_REVERSE_TILT = True

# About +/- 6 pixels horizontally and +/- 5 vertically at 640x480.
DEADBAND_X = 0.02
DEADBAND_Y = 0.02

# Gain above 1 reaches the selected speed limit before the image edge.
CONTROL_GAIN = 2.8

# Weight of the latest detection in the smoothed face position.
FACE_SMOOTHING = 0.70
FILTER_RESET_SECONDS = 0.5
LOST_FACE_RETURN_HOME_SECONDS = 3.0
