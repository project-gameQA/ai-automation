import os
from PIL import Image
import torch
from torchvision import transforms

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor()
])

ROOT = "ai_bot/sources"

for folder in os.listdir(ROOT):

    folder_path = os.path.join(ROOT, folder)

    frame_dir = os.path.join(folder_path, "frames")
    cache_dir = os.path.join(folder_path, "tensor_cache")

    if not os.path.exists(frame_dir):
        continue

    os.makedirs(cache_dir, exist_ok=True)

    files = sorted(os.listdir(frame_dir))

    for file in files:

        if not file.endswith(".jpg"):
            continue

        save_path = os.path.join(
            cache_dir,
            file.replace(".jpg", ".pt")
        )

        if os.path.exists(save_path):
            continue

        image = Image.open(
            os.path.join(frame_dir, file)
        ).convert("RGB")

        tensor = transform(image)

        torch.save(tensor, save_path)

    print(folder, "완료")