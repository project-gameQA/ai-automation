import os

import time
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split

from bc_dataset import WASDDataset
from bc_model import EfficientNetLSTM


torch.backends.cudnn.benchmark = True

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

DATASET_ROOT = "ai_bot/sources"
MODEL_PATH = "ai_bot/models"

SEQ_LEN = 4
IMAGE_SIZE = 224
BATCH_SIZE = 36
EPOCHS = 20
LEARNING_RATE = 3e-4
NUM_WORKERS = 12


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
) -> float:
    model.eval()
    total_loss = 0.0

    with torch.inference_mode():
        for images, labels in loader:
            images = images.to(DEVICE, non_blocking=True)
            labels = labels.to(DEVICE, non_blocking=True)

            logits = model(images)
            loss = criterion(logits, labels)

            total_loss += loss.item()

    return total_loss / max(len(loader), 1)


def main() -> None:
    print("device:", DEVICE)

    model_dir = os.path.dirname(MODEL_PATH)
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)

    dataset = WASDDataset(
        DATASET_ROOT,
        seq_len=SEQ_LEN,
        image_size=IMAGE_SIZE,
    )

    train_size = int(len(dataset) * 0.9)
    validation_size = len(dataset) - train_size

    train_dataset, validation_dataset = random_split(
        dataset,
        [train_size, validation_size],
        generator=torch.Generator().manual_seed(42),
    )

    pin_memory = DEVICE.type == "cuda"

    train_loader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        shuffle=True,
        num_workers=NUM_WORKERS,
        pin_memory=True,
        persistent_workers=True,
        prefetch_factor=4,
    )

    validation_loader = DataLoader(
        validation_dataset,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=True,
        persistent_workers=True,
        prefetch_factor=4,
    )

    print("train samples:", len(train_dataset))
    print("validation samples:", len(validation_dataset))

    model = EfficientNetLSTM().to(DEVICE)

    criterion = nn.BCEWithLogitsLoss()

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=LEARNING_RATE,
        weight_decay=1e-4,
    )

    scaler = torch.amp.GradScaler(
        "cuda",
        enabled=DEVICE.type == "cuda",
    )

    best_validation_loss = float("inf")
    print("train")
    for epoch in range(1, EPOCHS + 1):
        total_train_loss = 0.0
        epoch_start = time.perf_counter()
        model.train()

        for images, labels in train_loader:
            images = images.to(DEVICE, non_blocking=True)
            labels = labels.to(DEVICE, non_blocking=True)

            optimizer.zero_grad(set_to_none=True)

            with torch.amp.autocast(
                device_type=DEVICE.type,
                enabled=DEVICE.type == "cuda",
            ):
                logits = model(images)
                loss = criterion(logits, labels)

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)

            torch.nn.utils.clip_grad_norm_(
                model.parameters(),
                max_norm=5.0,
            )

            scaler.step(optimizer)
            scaler.update()
            
            total_train_loss += loss.item()

        train_loss = total_train_loss / max(len(train_loader), 1)

        validation_loss = evaluate(
            model,
            validation_loader,
            criterion,
        )
        epoch_seconds = time.perf_counter() - epoch_start
        epoch_minutes = epoch_seconds / 60

        print(
            f"Epoch {epoch}/{EPOCHS} | "
            f"train={train_loss:.6f} | "
            f"validation={validation_loss:.6f}"
            f"Loss: {train_loss:.4f} | "
            f"시간: {epoch_minutes:.2f}분"
        )

        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss
            torch.save(model.state_dict(), f"{MODEL_PATH}/train_2_{epoch}.pth")
            print(f"모델 저장: {MODEL_PATH}")


if __name__ == "__main__":
    main()