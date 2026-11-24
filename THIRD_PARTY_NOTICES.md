# Third-party notices

The root [MIT license](LICENSE) covers the original application and website
code by Felix and Artur. The photos and demo videos under `site/assets/`
are outside that grant. Third-party components are covered by their own terms.

## Models

| Component | How it is used | License and credit |
| --- | --- | --- |
| YuNet, `face_detection_yunet_2023mar.onnx` | The bundled face detector from the [OpenCV Model Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | MIT, copyright 2020 Shiqi Yu. The full [upstream notice](src/face_tracker/models/LICENSE) ships alongside the weights. Keep it when redistributing the weights. |
| Haar frontal-face cascade, `haarcascade_frontalface_default.xml` | Loaded from the installed OpenCV cascade data | Created by Rainer Lienhart. The file carries the Intel License Agreement for Open Source Computer Vision Library, copyright 2000 Intel Corporation. See the full notice in the [upstream cascade file](https://github.com/opencv/opencv/blob/4.10.0/data/haarcascades/haarcascade_frontalface_default.xml). |

## Runtime dependencies

These components are installed separately. Their package licenses and
notices apply to any copies included in a distribution.

| Component | How it is used | Upstream license |
| --- | --- | --- |
| OpenCV | Face detection, frame processing and JPEG encoding | [Apache-2.0 for the tested OpenCV 4.10 release](https://github.com/opencv/opencv/blob/4.10.0/LICENSE) |
| Picamera2 | Raspberry Pi camera capture | [BSD-2-Clause](https://github.com/raspberrypi/picamera2/blob/main/LICENSE) |
| NumPy | Image arrays | [BSD-3-Clause](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| smbus2 | I²C communication with the PCA9685 | [MIT](https://github.com/kplindegaard/smbus2/blob/master/LICENSE) |
| Python and its standard library | HTTP server, threading and other runtime utilities | [Python Software Foundation license and bundled notices](https://docs.python.org/3/license.html) |
