import os

import pandas as pd
import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision import transforms


class WASDDataset(Dataset):
    def __init__(
        self,
        root: str,
        seq_len: int = 4,
        image_size: int = 224,
    ):
        self.samples = []
        self.seq_len = seq_len

        self.transform = transforms.Compose([
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])

        if not os.path.isdir(root):
            raise FileNotFoundError(
                f"Dataset 경로가 없습니다: {root}"
            )

        required_columns = ["frame", "w", "a", "s", "d"]
        key_columns = ["w", "a", "s", "d"]

        for folder in sorted(os.listdir(root)):
            folder_path = os.path.join(root, folder)

            if not os.path.isdir(folder_path):
                continue

            csv_path = os.path.join(
                folder_path,
                f"{folder}.csv",
            )
            frame_dir = os.path.join(
                folder_path,
                "frames",
            )

            if not os.path.isfile(csv_path):
                continue

            if not os.path.isdir(frame_dir):
                continue

            df = pd.read_csv(csv_path)

            if not all(
                column in df.columns
                for column in required_columns
            ):
                raise ValueError(
                    f"{csv_path}: 필요한 열은 "
                    f"{required_columns} 입니다."
                )

            df = df[required_columns].copy()

            df["frame"] = pd.to_numeric(
                df["frame"],
                errors="raise",
            ).astype(int)

            df[key_columns] = (
                df[key_columns]
                .apply(pd.to_numeric, errors="raise")
                .astype("float32")
            )

            for end_index in range(
                seq_len - 1,
                len(df),
            ):
                start_index = (
                    end_index - seq_len + 1
                )

                frame_paths = [
                    os.path.join(
                        frame_dir,
                        f"{int(df.iloc[row_index]['frame']):06d}.jpg",
                    )
                    for row_index in range(
                        start_index,
                        end_index + 1,
                    )
                ]

                label = torch.tensor(
                    df.iloc[end_index][key_columns]
                    .to_numpy(dtype="float32"),
                    dtype=torch.float32,
                )

                self.samples.append(
                    (frame_paths, label)
                )

        if not self.samples:
            raise RuntimeError(
                "학습 가능한 샘플이 없습니다."
            )

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int):
        frame_paths, label = self.samples[index]

        images = []

        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                image = image.convert("RGB")
                images.append(
                    self.transform(image)
                )

        sequence = torch.stack(images)

        return sequence, label