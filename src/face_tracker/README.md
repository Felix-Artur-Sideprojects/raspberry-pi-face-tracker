# Simple Raspberry Pi Face Tracker

This folder contains the camera server, browser controls and servo driver
for the Raspberry Pi 4B.

## File layout

- [app.py](app.py) captures frames, tracks the largest face and serves the web interface.
- [config.py](config.py) holds camera settings and hardware calibration.
- [detectors.py](detectors.py) runs Haar or YuNet and returns boxes in camera coordinates.
- [servos.py](servos.py) converts image error to bounded pan and tilt commands over I²C.
- [static/index.html](static/index.html) shows the camera, controls and measurements.
- [models/](models/) contains the YuNet weights and their upstream licence.

## Run

Use Raspberry Pi OS with Python 3.10 or newer, Picamera2, NumPy, OpenCV
with the Haar cascade data and FaceDetectorYN support, and smbus2. Enable
the camera and I²C as needed for your setup. The original demo used OpenCV 4.10.

From the repository on the Pi, start with camera-only mode:

    cd src/face_tracker
    python3 app.py --no-servo

The HTTP server listens on the Pi at 127.0.0.1:8080. From a separate laptop
terminal, forward the port over SSH. Replace USER and PI_HOST with the Pi's
login name and address:

    ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 USER@PI_HOST

Keep the SSH session open and visit http://127.0.0.1:8080 in the laptop's
browser. If laptop port 8080 is already in use, replace the first port in
the SSH command and the browser address with a free port.

The live video and motor controls are meant for use over this tunnel.
Keep the HTTP service on loopback and use SSH to control access.

When the servos are connected and calibrated, start with `python3 app.py`
and follow the direction test below. Tracking starts off. Ctrl+C closes
the camera and requests that the PCA9685 turn its outputs off. YuNet is
the default, and the browser can switch to Haar during operation.

## How correction works

For the selected face centre (face_x, face_y):

    error_x = (face_x - image_centre_x) / half_image_width
    error_y = (face_y - image_centre_y) / half_image_height

Examples:

- error_x is positive when the face is right of centre.
- error_x is negative when the face is left of centre.
- error_y is positive when the face is below centre.
- error_y is negative when the face is above centre.

servos.py removes the small central deadband and turns the remaining errors
into angular velocities. An aggressive gain reaches the selected maximum speed
well before the image edge. The velocity is integrated using the measured loop
time, so changing camera FPS does not change the physical correction speed.

For the current mechanical assembly, both direction reversals are enabled by
default. If the servo horns are later remounted, Reverse pan and Reverse tilt
can still be changed in the web interface on the next camera frame.

## First direction test

1. Leave tracking OFF.
2. Use the four 5-degree manual buttons and note which physical direction each
   positive and negative movement produces.
3. Place one face slightly right of the white image-centre cross.
4. Start tracking at 10 degrees/second.
5. If the camera moves left, stop immediately and change Reverse pan.
6. Repeat above/below centre for Reverse tilt.
7. Increase speed only after both directions are correct.

The laser remains disconnected during all face-tracking tests.
