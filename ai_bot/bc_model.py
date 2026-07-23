import torch
import torch.nn as nn

from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights


class EfficientNetLSTM(nn.Module):

    def __init__(self):
        super().__init__()

        backbone = efficientnet_b0(
            weights=EfficientNet_B0_Weights.DEFAULT
        )

        self.feature = backbone.features
        self.pool = backbone.avgpool

        self.lstm = nn.LSTM(
            input_size=1280,
            hidden_size=256,
            num_layers=2,
            batch_first=True,
            dropout=0.2
        )

        self.dropout = nn.Dropout(0.3)

        self.fc = nn.Linear(256, 4)

    def forward(self, x):

        B, T, C, H, W = x.shape

        x = x.reshape(B * T, C, H, W)

        x = self.feature(x)
        x = self.pool(x)

        x = torch.flatten(x, 1)

        x = x.reshape(B, T, 1280)

        x, _ = self.lstm(x)

        x = x[:, -1]

        x = self.dropout(x)

        return self.fc(x)