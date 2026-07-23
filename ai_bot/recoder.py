import csv
import shutil
import threading
import time
from pathlib import Path

import cv2
import mss
import numpy as np
from pynput import keyboard
from win11toast import toast


DURATION = 5 * 60
FPS = 30

NAME = "maze08"

SAVE_DIR = Path("ai_bot/sources") / NAME
FRAME_DIR = SAVE_DIR / "frames"
CSV_PATH = SAVE_DIR / f"{NAME}.csv" 

MONITOR = {
    "top": 0,
    "left": 0,
    "width": 1920,
    "height": 1080,
}

pressed_keys = {
    "w": 0,
    "a": 0,
    "s": 0,
    "d": 0,
}

stop_requested = False
key_lock = threading.Lock()


def on_press(key) -> None:
    global stop_requested

    try:
        char = key.char.lower()

        if char in pressed_keys:
            with key_lock:
                pressed_keys[char] = 1

        elif char == "q":
            stop_requested = True

    except AttributeError:
        pass


def on_release(key) -> None:
    try:
        char = key.char.lower()

        if char in pressed_keys:
            with key_lock:
                pressed_keys[char] = 0

    except AttributeError:
        pass


def prepare_output_directory() -> None:
    if SAVE_DIR.exists():
        shutil.rmtree(SAVE_DIR)

    FRAME_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    global stop_requested

    prepare_output_directory()

    listener = keyboard.Listener(
        on_press=on_press,
        on_release=on_release,
    )
    listener.start()

    frame_index = 0
    frame_interval = 1.0 / FPS
    next_capture_time = time.perf_counter()
    start_time = next_capture_time

    toast("알람", "수집 시작")

    try:
        with (
            mss.mss() as sct,
            open(
                CSV_PATH,
                "w",
                newline="",
                encoding="utf-8",
            ) as csv_file,
        ):
            writer = csv.writer(csv_file)
            writer.writerow(["frame", "w", "a", "s", "d"])

            while not stop_requested:
                now = time.perf_counter()

                if now - start_time >= DURATION:
                    break

                if now < next_capture_time:
                    time.sleep(
                        min(
                            next_capture_time - now,
                            0.002,
                        )
                    )
                    continue

                screenshot = sct.grab(MONITOR)
                frame = np.asarray(screenshot)

                frame_bgr = cv2.cvtColor(
                    frame,
                    cv2.COLOR_BGRA2BGR,
                )

                with key_lock:
                    key_values = [
                        pressed_keys["w"],
                        pressed_keys["a"],
                        pressed_keys["s"],
                        pressed_keys["d"],
                    ]

                frame_path = (
                    FRAME_DIR
                    / f"{frame_index:06d}.jpg"
                )

                saved = cv2.imwrite(
                    str(frame_path),
                    frame_bgr,
                    [
                        cv2.IMWRITE_JPEG_QUALITY,
                        95,
                    ],
                )

                if not saved:
                    raise RuntimeError(
                        f"이미지 저장 실패: {frame_path}"
                    )
                elapsed = time.perf_counter() - start_time

                if elapsed >= DURATION:
                    print("시간 제한이 종료되었습니다.")
                    break
                writer.writerow([
                    frame_index,
                    *key_values,
                ])

                frame_index += 1
                next_capture_time += frame_interval

                if frame_index % FPS == 0:
                    csv_file.flush()

                if now - next_capture_time > frame_interval:
                    next_capture_time = now + frame_interval

    finally:
        listener.stop()
        listener.join()

        toast(
            "파이썬 알림",
            f"종료: {frame_index}개 프레임 저장",
        )

        print(f"저장된 프레임: {frame_index}")
        print(f"CSV 위치: {CSV_PATH}")


if __name__ == "__main__":
    main()