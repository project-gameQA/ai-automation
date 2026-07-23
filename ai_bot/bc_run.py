import time
from collections import deque

import cv2
import mss
import numpy as np
import pydirectinput
import torch
from PIL import Image
from torchvision import transforms

from bc_model import EfficientNetLSTM


DEVICE = torch.device("cuda")

MODEL_PATH = "ai_bot/models/train_2_2.pth"

SEQ_LEN = 4
CAPTURE_INTERVAL = 1 / 30
PRESS_THRESHOLD = 0.5
RELEASE_THRESHOLD = 0.3
PROB_HISTORY_SIZE = 2
# PRESS_THRESHOLD = 0.1
# RELEASE_THRESHOLD = 0.01
MIN_HOLD_TIME = 0.10

MONITOR = {
    "top": 0,
    "left": 0,
    "width": 1920,
    "height": 1080,
}

KEY_NAMES = ("w", "a", "s", "d")


transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225],
    ),
])


model = EfficientNetLSTM().to(DEVICE)

state_dict = torch.load(
    MODEL_PATH,
    map_location=DEVICE,
    weights_only=True,
)

model.load_state_dict(state_dict)
model.eval()


frame_buffer = deque(maxlen=SEQ_LEN)
prob_history = deque(maxlen=PROB_HISTORY_SIZE)

key_state = {
    key: False
    for key in KEY_NAMES
}

pressed_at = {
    key: 0.0
    for key in KEY_NAMES
}


def press_key(key: str) -> None:
    if key_state[key]:
        return

    pydirectinput.keyDown(key)
    key_state[key] = True
    pressed_at[key] = time.perf_counter()


def release_key(key: str) -> None:
    if not key_state[key]:
        return

    pydirectinput.keyUp(key)
    key_state[key] = False


def release_all_keys() -> None:
    for key in KEY_NAMES:
        try:
            pydirectinput.keyUp(key)
        except Exception:
            pass

        key_state[key] = False


def update_keys(probabilities: torch.Tensor) -> None:
    best_index = int(torch.argmax(probabilities).item())
    best_probability = probabilities[best_index].item()
    best_key = KEY_NAMES[best_index]

    if best_probability < PRESS_THRESHOLD:
        release_all_keys()
        return

    for key in KEY_NAMES:
        if key != best_key:
            release_key(key)

    press_key(best_key)


def capture_frame(sct: mss.mss) -> torch.Tensor:
    screenshot = sct.grab(MONITOR)
    frame = np.asarray(screenshot)

    frame = cv2.cvtColor(
        frame,
        cv2.COLOR_BGRA2RGB,
    )

    image = Image.fromarray(frame)

    return transform(image)


def predict() -> torch.Tensor:
    sequence = torch.stack(
        tuple(frame_buffer),
        dim=0,
    )

    sequence = sequence.unsqueeze(0).to(
        DEVICE,
        non_blocking=True,
    )

    with torch.inference_mode():
        logits = model(sequence)
        probabilities = torch.sigmoid(logits)[0]

    return probabilities.cpu()


def main() -> None:
    print("device:", DEVICE)
    print("model:", MODEL_PATH)
    print("실시간 추론 시작")
    print("종료: Ctrl+C")

    last_capture_time = 0.0

    with mss.mss() as sct:
        try:
            while True:
                now = time.perf_counter()
                remaining = CAPTURE_INTERVAL - (
                    now - last_capture_time
                )

                if remaining > 0:
                    time.sleep(min(remaining, 0.005))
                    continue

                last_capture_time = time.perf_counter()

                frame_buffer.append(
                    capture_frame(sct)
                )

                if len(frame_buffer) < SEQ_LEN:
                    continue
                        
                probabilities = predict()
                prob_history.append(probabilities)

                smoothed_probabilities = torch.stack(
                    tuple(prob_history),
                    dim=0,
                ).mean(dim=0)

                update_keys(smoothed_probabilities)

                print(
                    " ".join(
                        f"{key.upper()}={prob:.3f}"
                        for key, prob in zip(
                            KEY_NAMES,
                            smoothed_probabilities.tolist(),
                        )
                    )
                )
        except KeyboardInterrupt:
            print("\n실시간 추론 종료")

        finally:
            release_all_keys()


if __name__ == "__main__":
    main()