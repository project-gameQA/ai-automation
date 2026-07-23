from pathlib import Path

import torch
import torch.nn.functional as F

from bc_model import EfficientNetLSTM
from bc_dataset import WASDDataset
from torch.utils.data import DataLoader, random_split
DEVICE = torch.device("cuda")
MODEL_PATH = "ai_bot/model/best_model.pth"
DATASET_ROOT = "ai_bot/sources"
print("model path:", Path(MODEL_PATH).resolve())

dataset = WASDDataset(
    DATASET_ROOT,
    seq_len=4,
    image_size=224,
)
model = EfficientNetLSTM().to(DEVICE)
train_size = int(len(dataset) * 0.9)
validation_size = len(dataset) - train_size


state_dict = torch.load(
    MODEL_PATH,
    map_location=DEVICE,
    weights_only=True,
)
train_dataset, validation_dataset = random_split(
    dataset,
    [train_size, validation_size],
    generator=torch.Generator().manual_seed(42),
)
load_result = model.load_state_dict(state_dict)
print("load result:", load_result)

model.eval()


for index in [0, 1000, 5000, 10000]:
    sequence, label = train_dataset[index]

    sequence = sequence.unsqueeze(0).to(DEVICE)
    label = label.unsqueeze(0).to(DEVICE)

    with torch.inference_mode():
        logits = model(sequence)
        probabilities = torch.sigmoid(logits)

        loss = F.binary_cross_entropy_with_logits(
            logits,
            label,
        )

    print(f"\nindex: {index}")
    print("label:", label[0].cpu().tolist())
    print(
        "prediction:",
        [round(value, 4) for value in probabilities[0].cpu().tolist()],
    )
    print("loss:", loss.item())