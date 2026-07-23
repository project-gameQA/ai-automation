import os

import pandas as pd
import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision import transforms


class WASDDataset(Dataset):
    def __init__(
        self,
        root,
        seq_len=4,
        image_size=224,
    ):
        self.root = root
        self.seq_len = seq_len
        self.samples = []

        self.transform = transforms.Compose([
            transforms.Resize(
                (image_size, image_size)
            ),
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

        total_raw_rows = 0
        total_clean_rows = 0
        total_possible_sequences = 0
        total_missing_sequences = 0

        print(
            "Dataset root:",
            os.path.abspath(root),
        )

        for folder in sorted(os.listdir(root)):
            folder_path = os.path.join(
                root,
                folder,
            )

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
                print(
                    f"[건너뜀] CSV 없음: {csv_path}"
                )
                continue

            if not os.path.isdir(frame_dir):
                print(
                    f"[건너뜀] frames 폴더 없음: "
                    f"{frame_dir}"
                )
                continue

            df = pd.read_csv(csv_path)

            raw_rows = len(df)
            total_raw_rows += raw_rows

            # 열 이름 정리
            df.columns = (
                df.columns
                .astype(str)
                .str.replace(
                    "\ufeff",
                    "",
                    regex=False,
                )
                .str.strip()
                .str.lower()
            )

            # maze01~02:
            # frame, up, down, left, right
            #
            # maze03 이후:
            # frame, w, a, s, d
            df = df.rename(columns={
                "up": "w",
                "left": "a",
                "down": "s",
                "right": "d",
            })

            required_columns = [
                "frame",
                "w",
                "a",
                "s",
                "d",
            ]

            missing_columns = [
                column
                for column in required_columns
                if column not in df.columns
            ]

            if missing_columns:
                print(
                    f"[건너뜀] {folder}: "
                    f"필수 열 없음 {missing_columns}"
                )
                print(
                    "현재 열:",
                    df.columns.tolist(),
                )
                continue

            df = df[
                required_columns
            ].copy()

            # CSV 수정 후 frame은 숫자여야 함
            df["frame"] = pd.to_numeric(
                df["frame"],
                errors="coerce",
            )

            key_columns = [
                "w",
                "a",
                "s",
                "d",
            ]

            for key in key_columns:
                df[key] = pd.to_numeric(
                    df[key],
                    errors="coerce",
                )

            invalid_mask = (
                df[required_columns]
                .isna()
                .any(axis=1)
            )

            invalid_count = int(
                invalid_mask.sum()
            )

            if invalid_count > 0:
                print(
                    f"{folder}: 잘못된 행 "
                    f"{invalid_count}개"
                )

                print(
                    df.loc[
                        invalid_mask,
                        required_columns,
                    ].head(10)
                )

            df = df.dropna(
                subset=required_columns
            ).reset_index(drop=True)

            clean_rows = len(df)
            total_clean_rows += clean_rows

            df["frame"] = (
                df["frame"]
                .astype(int)
            )

            # 라벨을 0 또는 1로 정리
            df[key_columns] = (
                df[key_columns]
                .clip(0, 1)
                .astype(float)
            )

            possible_sequences = max(
                0,
                clean_rows - seq_len + 1,
            )

            total_possible_sequences += (
                possible_sequences
            )

            sample_count_before = len(
                self.samples
            )

            missing_sequences = 0
            first_missing_frame = None

            for end_index in range(
                seq_len - 1,
                clean_rows,
            ):
                frame_paths = []
                sequence_valid = True

                start_index = (
                    end_index - seq_len + 1
                )

                for row_index in range(
                    start_index,
                    end_index + 1,
                ):
                    frame_number = int(
                        df.iloc[row_index]["frame"]
                    )

                    frame_path = (
                        self._find_frame_path(
                            frame_dir,
                            frame_number,
                        )
                    )

                    if frame_path is None:
                        sequence_valid = False
                        missing_sequences += 1

                        if first_missing_frame is None:
                            first_missing_frame = (
                                frame_number
                            )

                        break

                    frame_paths.append(
                        frame_path
                    )

                if not sequence_valid:
                    continue

                label_values = (
                    df.iloc[end_index][
                        key_columns
                    ]
                    .to_numpy(
                        dtype="float32"
                    )
                )

                label = torch.tensor(
                    label_values,
                    dtype=torch.float32,
                )

                self.samples.append(
                    (
                        frame_paths,
                        label,
                    )
                )

            added_samples = (
                len(self.samples)
                - sample_count_before
            )

            total_missing_sequences += (
                missing_sequences
            )

            image_count = self._count_images(
                frame_dir
            )

            print(
                f"{folder} | "
                f"CSV 원본={raw_rows} | "
                f"정리 후={clean_rows} | "
                f"NaN 제거="
                f"{raw_rows - clean_rows} | "
                f"이미지={image_count} | "
                f"가능 시퀀스="
                f"{possible_sequences} | "
                f"등록={added_samples} | "
                f"이미지 누락 시퀀스="
                f"{missing_sequences}"
            )

            if first_missing_frame is not None:
                print(
                    f"  첫 이미지 누락 프레임: "
                    f"{first_missing_frame:06d}"
                )

        print(
            "\n===== Dataset 전체 통계 ====="
        )
        print(
            f"CSV 원본 행: {total_raw_rows}"
        )
        print(
            f"정리 후 행: {total_clean_rows}"
        )
        print(
            "이론상 가능한 시퀀스:",
            total_possible_sequences,
        )
        print(
            "이미지 누락 시퀀스:",
            total_missing_sequences,
        )
        print(
            f"최종 시퀀스: "
            f"{len(self.samples)}"
        )

        if not self.samples:
            raise RuntimeError(
                "등록된 학습 샘플이 없습니다. "
                "CSV와 이미지 경로를 확인하세요."
            )

    @staticmethod
    def _find_frame_path(
        frame_dir,
        frame_number,
    ):
        """
        프레임 번호에 대응하는 이미지 파일을 찾습니다.

        예:
        frame_number=0
        -> 000000.jpg
        """

        number = int(frame_number)

        stems = [
            f"{number:06d}",
            str(number),
        ]

        extensions = (
            ".jpg",
            ".jpeg",
            ".png",
            ".JPG",
            ".JPEG",
            ".PNG",
        )

        for stem in stems:
            for extension in extensions:
                path = os.path.join(
                    frame_dir,
                    stem + extension,
                )

                if os.path.isfile(path):
                    return path

        return None

    @staticmethod
    def _count_images(frame_dir):
        valid_extensions = (
            ".jpg",
            ".jpeg",
            ".png",
        )

        return sum(
            1
            for filename in os.listdir(frame_dir)
            if filename.lower().endswith(
                valid_extensions
            )
        )

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        frame_paths, label = (
            self.samples[index]
        )

        images = []

        for frame_path in frame_paths:
            try:
                with Image.open(
                    frame_path
                ) as image:
                    image = image.convert("RGB")
                    image = self.transform(image)

            except Exception as error:
                raise RuntimeError(
                    f"이미지 로드 실패: "
                    f"{frame_path}"
                ) from error

            images.append(image)

        sequence = torch.stack(
            images,
            dim=0,
        )

        return sequence, label