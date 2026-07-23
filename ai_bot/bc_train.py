import os

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split

from bc_dataset import WASDDataset
from bc_model import EfficientNetLSTM


# =========================================================
# 설정
# =========================================================

torch.backends.cudnn.benchmark = True

DEVICE = torch.device(
    "cuda" if torch.cuda.is_available() else "cpu"
)

DATASET_ROOT = "ai_bot/sources"
MODEL_PATH = "ai_bot/model/best_model.pth"

SEQ_LEN = 4
IMAGE_SIZE = 224

BATCH_SIZE = 8
EPOCHS = 20
LEARNING_RATE = 3e-4

NUM_WORKERS = 4

KEY_NAMES = ["W", "A", "S", "D"]


# =========================================================
# 검증 함수
# =========================================================

def evaluate(
    model: nn.Module,
    data_loader: DataLoader,
    criterion: nn.Module,
) -> tuple[float, torch.Tensor, torch.Tensor]:
    model.eval()

    total_loss = 0.0
    batch_count = 0

    predicted_positive = torch.zeros(
        4,
        device=DEVICE,
    )

    actual_positive = torch.zeros(
        4,
        device=DEVICE,
    )

    total_samples = 0

    with torch.inference_mode():
        for images, labels in data_loader:
            images = images.to(
                DEVICE,
                non_blocking=True,
            )

            labels = labels.to(
                DEVICE,
                non_blocking=True,
            )

            logits = model(images)
            loss = criterion(logits, labels)

            probs = torch.sigmoid(logits)

            predictions = (
                probs >= 0.5
            ).float()

            total_loss += loss.item()
            batch_count += 1

            predicted_positive += predictions.sum(dim=0)
            actual_positive += labels.sum(dim=0)

            total_samples += labels.size(0)

    average_loss = (
        total_loss / max(batch_count, 1)
    )

    predicted_positive_ratio = (
        predicted_positive
        / max(total_samples, 1)
    )

    actual_positive_ratio = (
        actual_positive
        / max(total_samples, 1)
    )

    return (
        average_loss,
        predicted_positive_ratio.cpu(),
        actual_positive_ratio.cpu(),
    )


# =========================================================
# 샘플 출력 함수
# =========================================================

def print_sample_predictions(
    model: nn.Module,
    data_loader: DataLoader,
    max_samples: int = 8,
) -> None:
    model.eval()

    images, labels = next(iter(data_loader))

    images = images.to(
        DEVICE,
        non_blocking=True,
    )

    labels = labels.to(
        DEVICE,
        non_blocking=True,
    )

    with torch.inference_mode():
        logits = model(images)
        probs = torch.sigmoid(logits)

    labels = labels.cpu()
    logits = logits.cpu()
    probs = probs.cpu()

    sample_count = min(
        max_samples,
        len(labels),
    )

    print("\n샘플 예측")

    for index in range(sample_count):
        label_text = ", ".join(
            f"{key}={value:.0f}"
            for key, value in zip(
                KEY_NAMES,
                labels[index].tolist(),
            )
        )

        prob_text = ", ".join(
            f"{key}={value:.3f}"
            for key, value in zip(
                KEY_NAMES,
                probs[index].tolist(),
            )
        )

        logit_text = ", ".join(
            f"{value:.3f}"
            for value in logits[index].tolist()
        )

        print(
            f"{index:02d} | "
            f"label [{label_text}] | "
            f"prob [{prob_text}] | "
            f"logit [{logit_text}]"
        )


# =========================================================
# 메인 학습
# =========================================================

def main() -> None:
    print("device:", DEVICE)

    os.makedirs(
        os.path.dirname(MODEL_PATH),
        exist_ok=True,
    )

    # -----------------------------------------------------
    # Dataset 생성
    # -----------------------------------------------------

    dataset = WASDDataset(
        DATASET_ROOT,
        seq_len=SEQ_LEN,
        image_size=IMAGE_SIZE,
    )

    if len(dataset) == 0:
        raise RuntimeError(
            "학습 가능한 데이터가 없습니다."
        )

    # -----------------------------------------------------
    # 실제 Dataset 라벨 분포 확인
    # -----------------------------------------------------

    all_labels = torch.stack([
        label
        for _, label in dataset.samples
    ])

    positive_counts = all_labels.sum(dim=0)
    negative_counts = (
        len(all_labels) - positive_counts
    )

    positive_ratios = all_labels.mean(dim=0)

    stop_count = (
        all_labels.sum(dim=1) == 0
    ).sum().item()

    stop_ratio = (
        stop_count / len(all_labels)
    )

    print("\nDataset 통계")
    print("전체 샘플:", len(dataset))

    for index, key in enumerate(KEY_NAMES):
        print(
            f"{key}: "
            f"{int(positive_counts[index].item())} "
            f"({positive_ratios[index].item():.2%})"
        )

    print(
        f"STOP: {stop_count} "
        f"({stop_ratio:.2%})"
    )

    # -----------------------------------------------------
    # pos_weight 계산
    #
    # 양성 데이터가 적은 키에 더 큰 가중치를 부여합니다.
    # -----------------------------------------------------

    pos_weight = (
        negative_counts
        / positive_counts.clamp(min=1)
    )

    print(
        "pos_weight:",
        [
            round(value, 4)
            for value in pos_weight.tolist()
        ],
    )

    criterion = nn.BCEWithLogitsLoss(
        pos_weight=pos_weight.to(DEVICE)
    )

    # -----------------------------------------------------
    # 학습/검증 분리
    # -----------------------------------------------------

    train_size = int(
        len(dataset) * 0.9
    )

    validation_size = (
        len(dataset) - train_size
    )

    split_generator = (
        torch.Generator()
        .manual_seed(42)
    )

    train_dataset, validation_dataset = random_split(
        dataset,
        [
            train_size,
            validation_size,
        ],
        generator=split_generator,
    )

    print("train samples:", len(train_dataset))
    print(
        "validation samples:",
        len(validation_dataset),
    )

    pin_memory = DEVICE.type == "cuda"

    train_loader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        shuffle=True,
        num_workers=NUM_WORKERS,
        pin_memory=pin_memory,
        persistent_workers=NUM_WORKERS > 0,
        prefetch_factor=2 if NUM_WORKERS > 0 else None,
    )

    validation_loader = DataLoader(
        validation_dataset,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=NUM_WORKERS,
        pin_memory=pin_memory,
        persistent_workers=NUM_WORKERS > 0,
        prefetch_factor=2 if NUM_WORKERS > 0 else None,
    )

    # -----------------------------------------------------
    # 모델
    # -----------------------------------------------------

    model = EfficientNetLSTM().to(DEVICE)

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

    # -----------------------------------------------------
    # 학습 반복
    # -----------------------------------------------------

    for epoch in range(EPOCHS):
        model.train()

        total_train_loss = 0.0
        train_batch_count = 0

        for images, labels in train_loader:
            images = images.to(
                DEVICE,
                non_blocking=True,
            )

            labels = labels.to(
                DEVICE,
                non_blocking=True,
            )

            optimizer.zero_grad(
                set_to_none=True
            )

            with torch.amp.autocast(
                device_type=DEVICE.type,
                enabled=DEVICE.type == "cuda",
            ):
                logits = model(images)
                loss = criterion(
                    logits,
                    labels,
                )

            scaler.scale(loss).backward()

            # 폭발적인 gradient 방지
            scaler.unscale_(optimizer)

            torch.nn.utils.clip_grad_norm_(
                model.parameters(),
                max_norm=5.0,
            )

            scaler.step(optimizer)
            scaler.update()

            total_train_loss += loss.item()
            train_batch_count += 1

        train_loss = (
            total_train_loss
            / max(train_batch_count, 1)
        )

        (
            validation_loss,
            predicted_positive_ratio,
            actual_positive_ratio,
        ) = evaluate(
            model,
            validation_loader,
            criterion,
        )

        print(
            f"\nEpoch {epoch + 1:02d}/{EPOCHS}"
        )

        print(
            f"train loss: {train_loss:.6f}"
        )

        print(
            f"validation loss: "
            f"{validation_loss:.6f}"
        )

        predicted_text = ", ".join(
            f"{key}={ratio:.2%}"
            for key, ratio in zip(
                KEY_NAMES,
                predicted_positive_ratio.tolist(),
            )
        )

        actual_text = ", ".join(
            f"{key}={ratio:.2%}"
            for key, ratio in zip(
                KEY_NAMES,
                actual_positive_ratio.tolist(),
            )
        )

        print(
            "predicted positive:",
            predicted_text,
        )

        print(
            "actual positive:   ",
            actual_text,
        )

        print_sample_predictions(
            model,
            validation_loader,
            max_samples=8,
        )

        # -------------------------------------------------
        # 가장 좋은 검증 모델 저장
        # -------------------------------------------------

        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss

            checkpoint = {
                "epoch": epoch + 1,
                "model_state_dict": (
                    model.state_dict()
                ),
                "optimizer_state_dict": (
                    optimizer.state_dict()
                ),
                "train_loss": train_loss,
                "validation_loss": (
                    validation_loss
                ),
                "pos_weight": pos_weight,
                "seq_len": SEQ_LEN,
                "image_size": IMAGE_SIZE,
            }

            torch.save(
                checkpoint,
                MODEL_PATH,
            )

            print(
                f"모델 저장: {MODEL_PATH}"
            )


if __name__ == "__main__":
    main()