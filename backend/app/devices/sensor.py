"""Table contact sensor: turns vibration into one tick per ball contact.

    python -m app.devices.sensor audio                 # contact mic on the sound card
    python -m app.devices.sensor audio --calibrate     # measure noise + bounces, pick threshold
    python -m app.devices.sensor serial --port /dev/ttyUSB0   # microcontroller + piezo

Audio: a contact mic taped under the table, read at 16 kHz. A contact is a sharp
peak above max(threshold, noise floor × NOISE_FACTOR); a refractory gap stops one
bounce ringing as several.
Serial: a microcontroller that does its own detection and prints one line per
contact (any text); each line is a tick, time-stamped on arrival.
"""

from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass, field

import numpy as np

from .camera import Api

SAMPLE_RATE = 16_000
BLOCK = 256  # 16 ms blocks
# Placeholders — run --calibrate on the real table.
THRESHOLD = 0.08  # absolute peak (full scale = 1.0)
NOISE_FACTOR = 6.0  # a contact must also be this many times the running noise floor
REFRACTORY_S = 0.06  # bounces are >~100 ms apart; ringing is shorter than this
NOISE_ALPHA = 0.02  # how fast the noise floor follows quiet blocks


@dataclass
class ImpulseDetector:
    threshold: float = THRESHOLD
    noise_factor: float = NOISE_FACTOR
    refractory_s: float = REFRACTORY_S
    sample_rate: int = SAMPLE_RATE
    noise_floor: float = 0.005
    last_tick: float = -1e9
    ticks: list[tuple[float, float]] = field(default_factory=list)

    @property
    def level(self) -> float:
        return max(self.threshold, self.noise_floor * self.noise_factor)

    def process(self, block: np.ndarray, block_start_ts: float) -> list[tuple[float, float]]:
        """block: mono float samples. Returns [(ts, peak)] for contacts in the block."""
        # First difference = crude high-pass: keeps the sharp click, drops rumble/hum.
        x = np.abs(np.diff(block, prepend=block[:1]))
        out = []
        level = self.level
        i = 0
        while i < len(x):
            if x[i] >= level:
                ts = block_start_ts + i / self.sample_rate
                if ts - self.last_tick >= self.refractory_s:
                    j = min(len(x), i + int(self.refractory_s * self.sample_rate))
                    peak = float(x[i:j].max())
                    out.append((ts, peak))
                    self.last_tick = ts
                    i = j
                    continue
            i += 1
        if not out:  # only quiet blocks teach the noise floor
            rms = float(np.sqrt(np.mean(x * x)))
            self.noise_floor += NOISE_ALPHA * (rms - self.noise_floor)
        self.ticks.extend(out)
        return out


def run_audio(api: Api, det: ImpulseDetector, device) -> None:
    import sounddevice as sd  # needs PortAudio (apt install libportaudio2)

    def callback(indata, frames, time_info, status):
        # Time of the block's first sample on the shared wall clock.
        start = time.time() - frames / SAMPLE_RATE
        for ts, peak in det.process(indata[:, 0].astype(np.float64), start):
            api.post_async("/api/capture/ticks", {"ts": ts, "strength": round(peak, 4)})

    with sd.InputStream(
        samplerate=SAMPLE_RATE, blocksize=BLOCK, channels=1, device=device, callback=callback
    ):
        print(f"listening (threshold {det.threshold}, ×{det.noise_factor} noise). Ctrl+C stops.")
        while True:
            time.sleep(1)


def run_serial(api: Api, port: str, baud: int) -> None:
    import serial

    with serial.Serial(port, baud, timeout=1) as s:
        print(f"reading contacts from {port}")
        while True:
            if s.readline().strip():
                api.post_async("/api/capture/ticks", {"ts": time.time()})


def record(seconds: float, device) -> np.ndarray:
    import sounddevice as sd

    data = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, device=device)
    sd.wait()
    return data[:, 0].astype(np.float64)


def suggest_threshold(quiet: np.ndarray, bounces: np.ndarray) -> dict:
    """Place the threshold between the loudest noise and the typical bounce."""
    q = np.abs(np.diff(quiet))
    b = np.abs(np.diff(bounces))
    noise_p999 = float(np.quantile(q, 0.999))
    win = int(0.1 * SAMPLE_RATE)
    peaks = sorted(
        float(b[i : i + win].max()) for i in range(0, len(b) - win, win) if b[i : i + win].max()
    )
    loud = [p for p in peaks if p > noise_p999 * 3] or peaks[-3:]
    bounce_median = statistics.median(loud) if loud else 0.0
    return {
        "noise_p99.9": round(noise_p999, 4),
        "bounce_median_peak": round(bounce_median, 4),
        "suggested_threshold": round(float(np.sqrt(noise_p999 * bounce_median)), 4),
        "separation": round(bounce_median / noise_p999, 1) if noise_p999 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("source", choices=["audio", "serial"])
    ap.add_argument("--device", default=None, help="audio input device (name or index)")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--noise-factor", type=float, default=NOISE_FACTOR)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--calibrate", action="store_true")
    a = ap.parse_args()
    device = int(a.device) if a.device and a.device.isdigit() else a.device

    if a.calibrate:
        input("Keep the table quiet for 5 s, then press Enter… ")
        quiet = record(5, device)
        input("Now bounce the ball on the table ~10 times over 8 s. Press Enter to start… ")
        result = suggest_threshold(quiet, record(8, device))
        for k, v in result.items():
            print(f"{k:>22}: {v}")
        print(
            "The threshold sits at the geometric mean of the loudest noise and a typical bounce. "
            f"Run with --threshold {result['suggested_threshold']}."
        )
        if result["separation"] is not None and result["separation"] < 4:
            print("Warning: bounces are barely louder than noise — move the mic or add damping.")
        return

    api = Api(a.api)
    api.post_async(
        "/api/capture/device-params",
        {
            "device": "sensor",
            "params": (
                {
                    "source": "contact mic",
                    "threshold": a.threshold,
                    "noise_factor": a.noise_factor,
                    "refractory_ms": REFRACTORY_S * 1000,
                }
                if a.source == "audio"
                else {"source": f"serial {a.port}", "detection": "on the microcontroller"}
            ),
        },
    )
    if a.source == "audio":
        run_audio(api, ImpulseDetector(a.threshold, a.noise_factor), device)
    else:
        run_serial(api, a.port, a.baud)


if __name__ == "__main__":
    main()
