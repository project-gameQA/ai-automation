import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.amp import GradScaler, autocast
from torch.utils.data import DataLoader, Dataset, random_split
from torchvision.models import (
    EfficientNet_B0_Weights,
    efficientnet_b0,
)


# ============================================================
# 설정
# ============================================================

@dataclass
class Config:
    # 데이터 경로
    data_root: str = "ai_bot/sources"
    csv_name: str = "*.csv"
    tensor_dir_name: str = "tensor_cache"

    # 결과 저장 폴더
    output_dir: str = "ai_bot_rgb_edge"

    # 입력 설정
    seq_len: int = 4
    image_size: int = 224

    # 학습 설정
    batch_size: int = 8
    num_workers: int = 0

    epochs: int = 30
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4

    train_ratio: float = 0.9
    seed: int = 42

    # 모델
    hidden_size: int = 256
    lstm_layers: int = 2
    dropout: float = 0.2

    # Canny
    use_auto_canny: bool = True
    canny_sigma: float = 0.33

    canny_low: int = 50
    canny_high: int = 150

    blur_kernel: int = 5
    dilate_iterations: int = 0

    # augmentation
    use_augmentation: bool = True
    augmentation_probability: float = 0.8

    brightness: float = 0.15
    contrast: float = 0.15
    saturation: float = 0.10

    # AMP
    use_amp: bool = True

    # gradient clipping
    max_grad_norm: float = 1.0

    # early stopping
    early_stopping_patience: int = 10


CONFIG = Config()


# ============================================================
# 랜덤 시드
# ============================================================

def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)

    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)


# ============================================================
# Canny Edge
# ============================================================

def automatic_canny(
    gray: np.ndarray,
    sigma: float,
) -> np.ndarray:
    median = float(np.median(gray))

    low = int(
        max(
            0,
            (1.0 - sigma) * median,
        )
    )

    high = int(
        min(
            255,
            (1.0 + sigma) * median,
        )
    )

    if high <= low:
        high = min(
            255,
            low + 30,
        )

    return cv2.Canny(
        gray,
        low,
        high,
    )


def rgb_tensor_to_edge(
    image: torch.Tensor,
    config: Config,
) -> torch.Tensor:
    """
    입력:
        image [3, H, W]
        float32
        범위 [0, 1]

    출력:
        edge [1, H, W]
        float32
        범위 [0, 1]
    """

    if image.ndim != 3:
        raise ValueError(
            f"Expected [C,H,W], got {tuple(image.shape)}"
        )

    if image.shape[0] != 3:
        raise ValueError(
            f"Expected 3 channels, got {image.shape[0]}"
        )

    rgb = (
        image.detach()
        .cpu()
        .permute(1, 2, 0)
        .contiguous()
        .numpy()
    )

    rgb_uint8 = np.clip(
        rgb * 255.0,
        0,
        255,
    ).astype(np.uint8)

    gray = cv2.cvtColor(
        rgb_uint8,
        cv2.COLOR_RGB2GRAY,
    )

    blur_kernel = max(
        1,
        config.blur_kernel,
    )

    if blur_kernel % 2 == 0:
        blur_kernel += 1

    if blur_kernel > 1:
        gray = cv2.GaussianBlur(
            gray,
            (blur_kernel, blur_kernel),
            0,
        )

    if config.use_auto_canny:
        edges = automatic_canny(
            gray,
            config.canny_sigma,
        )
    else:
        edges = cv2.Canny(
            gray,
            config.canny_low,
            config.canny_high,
        )

    if config.dilate_iterations > 0:
        kernel = np.ones(
            (3, 3),
            dtype=np.uint8,
        )

        edges = cv2.dilate(
            edges,
            kernel,
            iterations=config.dilate_iterations,
        )

    edge_tensor = (
        torch.from_numpy(edges)
        .float()
        .div(255.0)
        .unsqueeze(0)
    )

    return edge_tensor


# ============================================================
# Augmentation
# ============================================================

def adjust_brightness(
    sequence: torch.Tensor,
    factor: float,
) -> torch.Tensor:
    return torch.clamp(
        sequence * factor,
        0.0,
        1.0,
    )


def adjust_contrast(
    sequence: torch.Tensor,
    factor: float,
) -> torch.Tensor:
    mean = sequence.mean(
        dim=(-2, -1),
        keepdim=True,
    )

    return torch.clamp(
        (sequence - mean) * factor + mean,
        0.0,
        1.0,
    )


def adjust_saturation(
    sequence: torch.Tensor,
    factor: float,
) -> torch.Tensor:
    weights = torch.tensor(
        [0.299, 0.587, 0.114],
        dtype=sequence.dtype,
        device=sequence.device,
    ).view(1, 3, 1, 1)

    gray = (
        sequence * weights
    ).sum(
        dim=1,
        keepdim=True,
    )

    return torch.clamp(
        gray + factor * (sequence - gray),
        0.0,
        1.0,
    )


def augment_sequence(
    sequence: torch.Tensor,
    config: Config,
) -> torch.Tensor:
    """
    sequence shape: [T, 3, H, W]

    시퀀스의 모든 프레임에 같은 augmentation을 적용합니다.
    """

    if not config.use_augmentation:
        return sequence

    if random.random() > config.augmentation_probability:
        return sequence

    brightness_factor = random.uniform(
        1.0 - config.brightness,
        1.0 + config.brightness,
    )

    contrast_factor = random.uniform(
        1.0 - config.contrast,
        1.0 + config.contrast,
    )

    saturation_factor = random.uniform(
        1.0 - config.saturation,
        1.0 + config.saturation,
    )

    sequence = adjust_brightness(
        sequence,
        brightness_factor,
    )

    sequence = adjust_contrast(
        sequence,
        contrast_factor,
    )

    sequence = adjust_saturation(
        sequence,
        saturation_factor,
    )

    return sequence


# ============================================================
# Dataset
# ============================================================

class WASDEdgeDataset(Dataset):
    def __init__(
        self,
        config: Config,
        augment: bool = False,
    ):
        super().__init__()

        self.config = config
        self.augment = augment

        self.data_root = Path(
            config.data_root
        )

        self.csv_path = (
            self.data_root
            / config.csv_name
        )

        self.tensor_dir = (
            self.data_root
            / config.tensor_dir_name
        )

        self.samples: list[
            tuple[list[Path], torch.Tensor]
        ] = []

        self._validate_paths()
        self._load_samples()

    def _validate_paths(self) -> None:
        if not self.data_root.exists():
            raise FileNotFoundError(
                f"Data root not found: "
                f"{self.data_root.resolve()}"
            )

        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"CSV not found: "
                f"{self.csv_path.resolve()}"
            )

        if not self.tensor_dir.exists():
            raise FileNotFoundError(
                f"Tensor directory not found: "
                f"{self.tensor_dir.resolve()}"
            )

    def _load_samples(self) -> None:
        dataframe = pd.read_csv(
            self.csv_path
        )

        required_columns = {
            "frame",
            "w",
            "a",
            "s",
            "d",
        }

        missing_columns = (
            required_columns
            - set(dataframe.columns)
        )

        if missing_columns:
            raise ValueError(
                f"Missing CSV columns: {missing_columns}"
            )

        dataframe = dataframe.copy()

        dataframe[
            ["w", "a", "s", "d"]
        ] = (
            dataframe[
                ["w", "a", "s", "d"]
            ]
            .apply(
                pd.to_numeric,
                errors="coerce",
            )
            .fillna(0.0)
            .clip(0.0, 1.0)
            .astype(np.float32)
        )

        missing_tensor_count = 0

        for target_index in range(
            self.config.seq_len - 1,
            len(dataframe),
        ):
            start_index = (
                target_index
                - self.config.seq_len
                + 1
            )

            tensor_paths: list[Path] = []
            valid_sequence = True

            for frame_index in range(
                start_index,
                target_index + 1,
            ):
                frame_value = dataframe.iloc[
                    frame_index
                ]["frame"]

                tensor_filename = (
                    self._frame_to_tensor_name(
                        frame_value
                    )
                )

                tensor_path = (
                    self.tensor_dir
                    / tensor_filename
                )

                if not tensor_path.exists():
                    valid_sequence = False
                    missing_tensor_count += 1
                    break

                tensor_paths.append(
                    tensor_path
                )

            if not valid_sequence:
                continue

            label_array = (
                dataframe.iloc[
                    target_index
                ][["w", "a", "s", "d"]]
                .to_numpy(
                    dtype=np.float32
                )
            )

            label = torch.from_numpy(
                label_array
            )

            self.samples.append(
                (
                    tensor_paths,
                    label,
                )
            )

        if not self.samples:
            raise RuntimeError(
                "No valid sequence samples found."
            )

        print(
            f"CSV rows: {len(dataframe):,}"
        )

        print(
            f"Valid sequences: {len(self.samples):,}"
        )

        if missing_tensor_count > 0:
            print(
                f"Missing tensor encounters: "
                f"{missing_tensor_count:,}"
            )

    @staticmethod
    def _frame_to_tensor_name(
        frame_value,
    ) -> str:
        """
        지원 예시:

        000001.png -> 000001.pt
        000001.jpg -> 000001.pt
        000001.pt  -> 000001.pt
        1          -> 000001.pt
        """

        if isinstance(
            frame_value,
            str,
        ):
            value = frame_value.strip()

            path = Path(value)

            if path.suffix:
                return path.with_suffix(
                    ".pt"
                ).name

            try:
                frame_number = int(
                    float(value)
                )

                return (
                    f"{frame_number:06d}.pt"
                )

            except ValueError:
                return f"{value}.pt"

        frame_number = int(
            frame_value
        )

        return f"{frame_number:06d}.pt"

    def __len__(self) -> int:
        return len(self.samples)

    def _load_rgb_tensor(
        self,
        tensor_path: Path,
    ) -> torch.Tensor:
        image = torch.load(
            tensor_path,
            map_location="cpu",
            weights_only=True,
        )

        if not isinstance(
            image,
            torch.Tensor,
        ):
            raise TypeError(
                f"Expected torch.Tensor: "
                f"{tensor_path}"
            )

        image = image.float()

        if image.ndim != 3:
            raise ValueError(
                f"Expected 3D tensor, got "
                f"{tuple(image.shape)}: "
                f"{tensor_path}"
            )

        # HWC -> CHW
        if (
            image.shape[0] != 3
            and image.shape[-1] == 3
        ):
            image = image.permute(
                2,
                0,
                1,
            )

        if image.shape[0] != 3:
            raise ValueError(
                f"Expected RGB tensor, got "
                f"{tuple(image.shape)}: "
                f"{tensor_path}"
            )

        # 0~255 텐서 대응
        if image.max().item() > 1.5:
            image = image / 255.0

        image = image.clamp(
            0.0,
            1.0,
        )

        if (
            image.shape[1]
            != self.config.image_size
            or image.shape[2]
            != self.config.image_size
        ):
            image = F.interpolate(
                image.unsqueeze(0),
                size=(
                    self.config.image_size,
                    self.config.image_size,
                ),
                mode="bilinear",
                align_corners=False,
            ).squeeze(0)

        return image

    def __getitem__(
        self,
        index: int,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        tensor_paths, label = (
            self.samples[index]
        )

        rgb_frames: list[
            torch.Tensor
        ] = []

        for tensor_path in tensor_paths:
            image = self._load_rgb_tensor(
                tensor_path
            )

            rgb_frames.append(
                image
            )

        # [T, 3, H, W]
        rgb_sequence = torch.stack(
            rgb_frames,
            dim=0,
        )

        if self.augment:
            rgb_sequence = augment_sequence(
                rgb_sequence,
                self.config,
            )

        rgb_edge_frames: list[
            torch.Tensor
        ] = []

        for rgb_image in rgb_sequence:
            edge_image = rgb_tensor_to_edge(
                rgb_image,
                self.config,
            )

            # [4, H, W]
            rgb_edge_image = torch.cat(
                [
                    rgb_image,
                    edge_image,
                ],
                dim=0,
            )

            rgb_edge_frames.append(
                rgb_edge_image
            )

        # [T, 4, H, W]
        sequence = torch.stack(
            rgb_edge_frames,
            dim=0,
        )

        return sequence, label.clone()


# ============================================================
# 모델
# ============================================================

class EfficientNetEdgeLSTM(nn.Module):
    def __init__(
        self,
        hidden_size: int,
        lstm_layers: int,
        dropout: float,
    ):
        super().__init__()

        backbone = efficientnet_b0(
            weights=(
                EfficientNet_B0_Weights.DEFAULT
            )
        )

        old_conv = backbone.features[0][0]

        new_conv = nn.Conv2d(
            in_channels=4,
            out_channels=old_conv.out_channels,
            kernel_size=old_conv.kernel_size,
            stride=old_conv.stride,
            padding=old_conv.padding,
            dilation=old_conv.dilation,
            groups=old_conv.groups,
            bias=old_conv.bias is not None,
            padding_mode=old_conv.padding_mode,
        )

        with torch.no_grad():
            # 기존 RGB 가중치
            new_conv.weight[
                :, :3
            ].copy_(
                old_conv.weight
            )

            # 네 번째 Edge 채널 초기화
            new_conv.weight[
                :, 3:4
            ].copy_(
                old_conv.weight.mean(
                    dim=1,
                    keepdim=True,
                )
            )

            if old_conv.bias is not None:
                new_conv.bias.copy_(
                    old_conv.bias
                )

        backbone.features[0][0] = (
            new_conv
        )

        self.features = (
            backbone.features
        )

        self.avgpool = (
            backbone.avgpool
        )

        self.lstm = nn.LSTM(
            input_size=1280,
            hidden_size=hidden_size,
            num_layers=lstm_layers,
            batch_first=True,
            dropout=(
                dropout
                if lstm_layers > 1
                else 0.0
            ),
        )

        self.dropout = nn.Dropout(
            dropout
        )

        self.classifier = nn.Linear(
            hidden_size,
            4,
        )

    def forward(
        self,
        x: torch.Tensor,
    ) -> torch.Tensor:
        """
        x: [B, T, 4, H, W]
        """

        if x.ndim != 5:
            raise ValueError(
                f"Expected [B,T,C,H,W], "
                f"got {tuple(x.shape)}"
            )

        batch_size, seq_len, channels, height, width = (
            x.shape
        )

        if channels != 4:
            raise ValueError(
                f"Expected 4 channels, "
                f"got {channels}"
            )

        x = x.reshape(
            batch_size * seq_len,
            channels,
            height,
            width,
        )

        x = self.features(x)
        x = self.avgpool(x)
        x = torch.flatten(
            x,
            start_dim=1,
        )

        x = x.reshape(
            batch_size,
            seq_len,
            1280,
        )

        x, _ = self.lstm(x)

        # 마지막 프레임의 LSTM 출력
        x = x[:, -1, :]

        x = self.dropout(x)

        logits = self.classifier(x)

        return logits


# ============================================================
# 학습
# ============================================================

def train_one_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    scaler: GradScaler,
    device: torch.device,
    config: Config,
) -> float:
    model.train()

    total_loss = 0.0
    total_samples = 0

    for batch_index, (
        sequences,
        labels,
    ) in enumerate(loader):
        sequences = sequences.to(
            device,
            non_blocking=True,
        )

        labels = labels.to(
            device,
            non_blocking=True,
        )

        optimizer.zero_grad(
            set_to_none=True
        )

        amp_enabled = (
            config.use_amp
            and device.type == "cuda"
        )

        with autocast(
            device_type=device.type,
            enabled=amp_enabled,
        ):
            logits = model(
                sequences
            )

            loss = criterion(
                logits,
                labels,
            )

        scaler.scale(
            loss
        ).backward()

        if config.max_grad_norm > 0:
            scaler.unscale_(
                optimizer
            )

            torch.nn.utils.clip_grad_norm_(
                model.parameters(),
                config.max_grad_norm,
            )

        scaler.step(
            optimizer
        )

        scaler.update()

        batch_size = (
            sequences.shape[0]
        )

        total_loss += (
            loss.item()
            * batch_size
        )

        total_samples += batch_size

        if (
            batch_index % 100 == 0
            or batch_index
            == len(loader) - 1
        ):
            print(
                f"  batch "
                f"{batch_index + 1}/"
                f"{len(loader)} "
                f"| loss={loss.item():.6f}"
            )

    return (
        total_loss
        / max(total_samples, 1)
    )


# ============================================================
# 검증
# ============================================================

@torch.inference_mode()
def validate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    config: Config,
) -> tuple[float, dict[str, float]]:
    model.eval()

    total_loss = 0.0
    total_samples = 0

    true_positive = torch.zeros(
        4,
        dtype=torch.float64,
    )

    false_positive = torch.zeros(
        4,
        dtype=torch.float64,
    )

    false_negative = torch.zeros(
        4,
        dtype=torch.float64,
    )

    correct = torch.zeros(
        4,
        dtype=torch.float64,
    )

    total_per_key = torch.zeros(
        4,
        dtype=torch.float64,
    )

    amp_enabled = (
        config.use_amp
        and device.type == "cuda"
    )

    for sequences, labels in loader:
        sequences = sequences.to(
            device,
            non_blocking=True,
        )

        labels = labels.to(
            device,
            non_blocking=True,
        )

        with autocast(
            device_type=device.type,
            enabled=amp_enabled,
        ):
            logits = model(
                sequences
            )

            loss = criterion(
                logits,
                labels,
            )

        probabilities = torch.sigmoid(
            logits
        )

        predictions = (
            probabilities >= 0.5
        ).float()

        batch_size = (
            sequences.shape[0]
        )

        total_loss += (
            loss.item()
            * batch_size
        )

        total_samples += batch_size

        correct += (
            predictions == labels
        ).sum(
            dim=0
        ).cpu().double()

        total_per_key += batch_size

        true_positive += (
            (predictions == 1)
            & (labels == 1)
        ).sum(
            dim=0
        ).cpu().double()

        false_positive += (
            (predictions == 1)
            & (labels == 0)
        ).sum(
            dim=0
        ).cpu().double()

        false_negative += (
            (predictions == 0)
            & (labels == 1)
        ).sum(
            dim=0
        ).cpu().double()

    accuracy = (
        correct
        / total_per_key.clamp(
            min=1
        )
    )

    precision = (
        true_positive
        / (
            true_positive
            + false_positive
        ).clamp(
            min=1
        )
    )

    recall = (
        true_positive
        / (
            true_positive
            + false_negative
        ).clamp(
            min=1
        )
    )

    f1 = (
        2
        * precision
        * recall
        / (
            precision
            + recall
        ).clamp(
            min=1e-8
        )
    )

    metrics: dict[
        str,
        float,
    ] = {}

    key_names = [
        "W",
        "A",
        "S",
        "D",
    ]

    for key_index, key_name in enumerate(
        key_names
    ):
        metrics[
            f"{key_name}_accuracy"
        ] = float(
            accuracy[key_index]
        )

        metrics[
            f"{key_name}_precision"
        ] = float(
            precision[key_index]
        )

        metrics[
            f"{key_name}_recall"
        ] = float(
            recall[key_index]
        )

        metrics[
            f"{key_name}_f1"
        ] = float(
            f1[key_index]
        )

    metrics["mean_accuracy"] = float(
        accuracy.mean()
    )

    metrics["mean_f1"] = float(
        f1.mean()
    )

    validation_loss = (
        total_loss
        / max(total_samples, 1)
    )

    return (
        validation_loss,
        metrics,
    )


# ============================================================
# 체크포인트 저장
# ============================================================

def save_checkpoint(
    path: Path,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler,
    scaler: GradScaler,
    epoch: int,
    train_loss: float,
    validation_loss: float,
    metrics: dict[str, float],
    config: Config,
) -> None:
    torch.save(
        {
            "epoch": epoch,
            "model_state_dict": (
                model.state_dict()
            ),
            "optimizer_state_dict": (
                optimizer.state_dict()
            ),
            "scheduler_state_dict": (
                scheduler.state_dict()
            ),
            "scaler_state_dict": (
                scaler.state_dict()
            ),
            "train_loss": train_loss,
            "validation_loss": (
                validation_loss
            ),
            "metrics": metrics,
            "config": asdict(
                config
            ),
        },
        path,
    )


# ============================================================
# 메인
# ============================================================

def main() -> None:
    config = CONFIG

    set_seed(
        config.seed
    )

    output_dir = Path(
        config.output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        output_dir / "config.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            asdict(config),
            file,
            ensure_ascii=False,
            indent=2,
        )

    device = torch.device(
        "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )

    print("=" * 70)
    print("RGB + Edge WASD Training")
    print("=" * 70)
    print("Device:", device)
    print(
        "Data root:",
        Path(config.data_root).resolve(),
    )

    if device.type == "cuda":
        print(
            "GPU:",
            torch.cuda.get_device_name(0),
        )

        torch.backends.cudnn.benchmark = (
            True
        )

        torch.set_float32_matmul_precision(
            "high"
        )

    # augmentation 없는 원본 dataset
    base_dataset = WASDEdgeDataset(
        config=config,
        augment=False,
    )

    total_size = len(
        base_dataset
    )

    train_size = int(
        total_size
        * config.train_ratio
    )

    validation_size = (
        total_size
        - train_size
    )

    if train_size < 1:
        raise RuntimeError(
            "Training set is empty."
        )

    if validation_size < 1:
        raise RuntimeError(
            "Validation set is empty."
        )

    generator = (
        torch.Generator()
        .manual_seed(
            config.seed
        )
    )

    train_subset, validation_subset = (
        random_split(
            base_dataset,
            [
                train_size,
                validation_size,
            ],
            generator=generator,
        )
    )

    # 동일한 샘플 순서를 가진 augmentation dataset
    augmented_dataset = WASDEdgeDataset(
        config=config,
        augment=(
            config.use_augmentation
        ),
    )

    train_subset.dataset = (
        augmented_dataset
    )

    print(
        f"Train samples: "
        f"{len(train_subset):,}"
    )

    print(
        f"Validation samples: "
        f"{len(validation_subset):,}"
    )

    train_loader = DataLoader(
        train_subset,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=config.num_workers,
        pin_memory=(
            device.type == "cuda"
        ),
        persistent_workers=(
            config.num_workers > 0
        ),
        drop_last=False,
    )

    validation_loader = DataLoader(
        validation_subset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        pin_memory=(
            device.type == "cuda"
        ),
        persistent_workers=(
            config.num_workers > 0
        ),
        drop_last=False,
    )

    model = EfficientNetEdgeLSTM(
        hidden_size=(
            config.hidden_size
        ),
        lstm_layers=(
            config.lstm_layers
        ),
        dropout=config.dropout,
    ).to(
        device
    )

    criterion = (
        nn.BCEWithLogitsLoss()
    )

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=(
            config.weight_decay
        ),
    )

    scheduler = (
        torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode="min",
            factor=0.5,
            patience=3,
            min_lr=1e-6,
        )
    )

    scaler = GradScaler(
        device=device.type,
        enabled=(
            config.use_amp
            and device.type == "cuda"
        ),
    )

    best_validation_loss = (
        math.inf
    )

    epochs_without_improvement = 0

    history: list[dict] = []

    for epoch in range(
        1,
        config.epochs + 1,
    ):
        epoch_start = (
            time.perf_counter()
        )

        current_lr = (
            optimizer.param_groups[0]["lr"]
        )

        print()
        print("=" * 70)

        print(
            f"Epoch {epoch}/"
            f"{config.epochs} "
            f"| lr={current_lr:.8f}"
        )

        print("=" * 70)

        train_loss = train_one_epoch(
            model=model,
            loader=train_loader,
            optimizer=optimizer,
            criterion=criterion,
            scaler=scaler,
            device=device,
            config=config,
        )

        validation_loss, metrics = (
            validate(
                model=model,
                loader=validation_loader,
                criterion=criterion,
                device=device,
                config=config,
            )
        )

        scheduler.step(
            validation_loss
        )

        epoch_seconds = (
            time.perf_counter()
            - epoch_start
        )

        print(
            f"Train loss: "
            f"{train_loss:.6f}"
        )

        print(
            f"Validation loss: "
            f"{validation_loss:.6f}"
        )

        print(
            f"Mean accuracy: "
            f"{metrics['mean_accuracy']:.4f}"
        )

        print(
            f"Mean F1: "
            f"{metrics['mean_f1']:.4f}"
        )

        for key_name in [
            "W",
            "A",
            "S",
            "D",
        ]:
            print(
                f"{key_name} | "
                f"accuracy="
                f"{metrics[f'{key_name}_accuracy']:.4f} "
                f"precision="
                f"{metrics[f'{key_name}_precision']:.4f} "
                f"recall="
                f"{metrics[f'{key_name}_recall']:.4f} "
                f"f1="
                f"{metrics[f'{key_name}_f1']:.4f}"
            )

        print(
            f"Epoch time: "
            f"{epoch_seconds:.1f}s"
        )

        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "validation_loss": (
                    validation_loss
                ),
                "learning_rate": (
                    current_lr
                ),
                "epoch_seconds": (
                    epoch_seconds
                ),
                **metrics,
            }
        )

        pd.DataFrame(
            history
        ).to_csv(
            output_dir
            / "history.csv",
            index=False,
        )

        save_checkpoint(
            path=(
                output_dir
                / "last_checkpoint.pth"
            ),
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            epoch=epoch,
            train_loss=train_loss,
            validation_loss=(
                validation_loss
            ),
            metrics=metrics,
            config=config,
        )

        torch.save(
            model.state_dict(),
            output_dir
            / "last_model.pth",
        )

        if (
            validation_loss
            < best_validation_loss
        ):
            best_validation_loss = (
                validation_loss
            )

            epochs_without_improvement = 0

            save_checkpoint(
                path=(
                    output_dir
                    / "best_checkpoint.pth"
                ),
                model=model,
                optimizer=optimizer,
                scheduler=scheduler,
                scaler=scaler,
                epoch=epoch,
                train_loss=train_loss,
                validation_loss=(
                    validation_loss
                ),
                metrics=metrics,
                config=config,
            )

            torch.save(
                model.state_dict(),
                output_dir
                / "best_model.pth",
            )

            print(
                "Best model saved: "
                f"{best_validation_loss:.6f}"
            )

        else:
            epochs_without_improvement += 1

            print(
                "No improvement: "
                f"{epochs_without_improvement}/"
                f"{config.early_stopping_patience}"
            )

        if (
            epochs_without_improvement
            >= config.early_stopping_patience
        ):
            print(
                "Early stopping."
            )
            break

    print()
    print("=" * 70)
    print("Training complete")
    print(
        "Best validation loss:",
        best_validation_loss,
    )

    print(
        "Best model:",
        (
            output_dir
            / "best_model.pth"
        ).resolve(),
    )
    print("=" * 70)


if __name__ == "__main__":
    main()