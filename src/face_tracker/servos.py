"""PCA9685 driver and proportional pan/tilt control.

Positive image errors point right and down. Angles are commanded positions;
the MG995 servos provide no position feedback.
"""

from __future__ import annotations

import math
import time

from smbus2 import SMBus

import config


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def move_towards(current: float, target: float, maximum_step: float) -> float:
    return current + clamp(target - current, -maximum_step, maximum_step)


def error_outside_deadband(error: float, deadband: float) -> float:
    """Remove the deadband and rescale to -1..1 without a speed jump."""

    magnitude = abs(error)
    if magnitude <= deadband:
        return 0.0
    scaled = (magnitude - deadband) / (1.0 - deadband)
    return math.copysign(clamp(scaled, 0.0, 1.0), error)


class PCA9685:
    MODE1 = 0x00
    MODE2 = 0x01
    PRESCALE = 0xFE
    LED0_ON_L = 0x06

    def __init__(self, bus: SMBus) -> None:
        self.bus = bus

    def configure(self) -> None:
        """Set 50 Hz and wake the PCA9685 oscillator."""

        prescale = round(25_000_000 / (4096 * config.PWM_FREQUENCY_HZ)) - 1
        old_mode = self.bus.read_byte_data(config.PCA9685_ADDRESS, self.MODE1)
        # The prescale register can only be changed while the oscillator sleeps.
        sleep_mode = (old_mode & ~0x80) | 0x10

        self.bus.write_byte_data(config.PCA9685_ADDRESS, self.MODE1, sleep_mode)
        self.bus.write_byte_data(config.PCA9685_ADDRESS, self.PRESCALE, prescale)

        wake_mode = sleep_mode & ~0x10
        self.bus.write_byte_data(config.PCA9685_ADDRESS, self.MODE1, wake_mode)
        time.sleep(0.005)
        self.bus.write_byte_data(
            config.PCA9685_ADDRESS, self.MODE1, wake_mode | 0x80 | 0x20
        )
        self.bus.write_byte_data(config.PCA9685_ADDRESS, self.MODE2, 0x04)

    def set_pulse_us(self, channel: int, pulse_us: int) -> None:
        ticks = round(pulse_us * config.PWM_FREQUENCY_HZ * 4096 / 1_000_000)
        register = self.LED0_ON_L + 4 * channel
        self.bus.write_i2c_block_data(
            config.PCA9685_ADDRESS,
            register,
            [0, 0, ticks & 0xFF, ticks >> 8],
        )

    def disable(self, channel: int) -> None:
        register = self.LED0_ON_L + 4 * channel
        # Bit 4 of LEDn_OFF_H forces this channel fully off.
        self.bus.write_i2c_block_data(
            config.PCA9685_ADDRESS, register, [0, 0, 0, 0x10]
        )


class PanTiltServos:
    """Commanded positions, movement limits, and PWM output."""

    def __init__(self, hardware_enabled: bool = True) -> None:
        self.hardware_enabled = hardware_enabled
        self.available = False
        self.outputs_active = False
        self.error: str | None = None
        self.pan = config.PAN_HOME_DEGREES
        self.tilt = config.TILT_HOME_DEGREES
        self.pan_rate_dps = 0.0
        self.tilt_rate_dps = 0.0
        self.bus: SMBus | None = None
        self.pca: PCA9685 | None = None

    def connect(self) -> None:
        if not self.hardware_enabled:
            self.error = "Servo hardware disabled with --no-servo."
            return
        try:
            self.bus = SMBus(config.I2C_BUS)
            self.pca = PCA9685(self.bus)
            self.pca.configure()
            self.available = True
        except OSError as error:
            self.error = f"PCA9685 connection failed: {error}"
            self.close()

    @staticmethod
    def _angle_to_pulse(angle: float, low: float, high: float) -> int:
        fraction = (angle - low) / (high - low)
        return round(
            config.SERVO_MIN_PULSE_US
            + fraction
            * (config.SERVO_MAX_PULSE_US - config.SERVO_MIN_PULSE_US)
        )

    def write(self) -> None:
        if not self.available or self.pca is None:
            return
        try:
            self.pca.set_pulse_us(
                config.PAN_CHANNEL,
                self._angle_to_pulse(
                    self.pan, config.PAN_MIN_DEGREES, config.PAN_MAX_DEGREES
                ),
            )
            self.pca.set_pulse_us(
                config.TILT_CHANNEL,
                self._angle_to_pulse(
                    self.tilt, config.TILT_MIN_DEGREES, config.TILT_MAX_DEGREES
                ),
            )
            self.outputs_active = True
        except OSError as error:
            self.error = f"PCA9685 write failed: {error}"

    def track(
        self,
        error_x: float,
        error_y: float,
        elapsed_seconds: float,
        speed_dps: float,
        reverse_pan: bool,
        reverse_tilt: bool,
    ) -> None:
        """Convert image errors to angular velocities and integrate over time."""

        pan_fraction = clamp(
            error_outside_deadband(error_x, config.DEADBAND_X)
            * config.CONTROL_GAIN,
            -1.0,
            1.0,
        )
        tilt_fraction = clamp(
            error_outside_deadband(error_y, config.DEADBAND_Y)
            * config.CONTROL_GAIN,
            -1.0,
            1.0,
        )

        pan_direction = -1.0 if reverse_pan else 1.0
        tilt_direction = -1.0 if reverse_tilt else 1.0

        self.pan_rate_dps = pan_direction * pan_fraction * speed_dps
        self.tilt_rate_dps = tilt_direction * tilt_fraction * speed_dps

        self.pan = clamp(
            self.pan + self.pan_rate_dps * elapsed_seconds,
            config.PAN_MIN_DEGREES,
            config.PAN_MAX_DEGREES,
        )
        self.tilt = clamp(
            self.tilt + self.tilt_rate_dps * elapsed_seconds,
            config.TILT_MIN_DEGREES,
            config.TILT_MAX_DEGREES,
        )
        self.write()

    def step_home(self, elapsed_seconds: float, speed_dps: float) -> bool:
        """Move both axes toward home. Return True once home is reached."""

        self.pan_rate_dps = 0.0
        self.tilt_rate_dps = 0.0
        step = speed_dps * elapsed_seconds
        self.pan = move_towards(self.pan, config.PAN_HOME_DEGREES, step)
        self.tilt = move_towards(self.tilt, config.TILT_HOME_DEGREES, step)
        self.write()
        return (
            abs(self.pan - config.PAN_HOME_DEGREES) < 0.05
            and abs(self.tilt - config.TILT_HOME_DEGREES) < 0.05
        )

    def nudge(self, axis: str, degrees: float) -> None:
        self.pan_rate_dps = 0.0
        self.tilt_rate_dps = 0.0
        if axis == "pan":
            self.pan = clamp(
                self.pan + degrees,
                config.PAN_MIN_DEGREES,
                config.PAN_MAX_DEGREES,
            )
        elif axis == "tilt":
            self.tilt = clamp(
                self.tilt + degrees,
                config.TILT_MIN_DEGREES,
                config.TILT_MAX_DEGREES,
            )
        else:
            raise ValueError("axis must be 'pan' or 'tilt'")
        self.write()

    def disable_outputs(self) -> bool:
        self.pan_rate_dps = 0.0
        self.tilt_rate_dps = 0.0
        disabled = True
        if self.pca is not None:
            # Try both channels even if one I²C write fails.
            for channel in (config.PAN_CHANNEL, config.TILT_CHANNEL):
                try:
                    self.pca.disable(channel)
                except OSError as error:
                    self.error = f"PCA9685 could not turn channel {channel} off: {error}"
                    disabled = False
        if disabled:
            self.outputs_active = False
        return disabled

    def close(self) -> None:
        self.disable_outputs()
        if self.bus is not None:
            self.bus.close()
        self.bus = None
        self.pca = None
        self.available = False
