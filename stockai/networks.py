from copy import deepcopy
import math

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


class Network(nn.Module):
    def __init__(self, name, dimensions, sequence):
        super().__init__()
        width = 128
        self.name = name
        if name == "Transformer":
            self.embedding = nn.Linear(dimensions, width)
            positions = torch.arange(sequence, dtype=torch.float32).unsqueeze(1)
            freq = torch.exp(torch.arange(0, width, 2)*(-math.log(10000)/width))
            encoding = torch.zeros(sequence, width)
            encoding[:, ::2], encoding[:, 1::2] = torch.sin(positions*freq), torch.cos(positions*freq)
            self.register_buffer("positions", encoding)
            layer = nn.TransformerEncoderLayer(width, 4, width*2, .2, batch_first=True)
            self.encoder = nn.TransformerEncoder(layer, 3)
        else:
            recurrent = nn.LSTM if name == "LSTM" else nn.GRU
            self.encoder = recurrent(dimensions, width, num_layers=2, batch_first=True, dropout=.2)
        self.classify = nn.Linear(width, 3)
        self.regress = nn.Linear(width, 3)

    def forward(self, x):
        if self.name == "Transformer":
            encoded = self.encoder(self.embedding(x) + self.positions[:x.shape[1]]).mean(dim=1)
        else:
            encoded = self.encoder(x)[0][:, -1]
        return self.classify(encoded), self.regress(encoded)


class NeuralModel:
    def __init__(self, name, settings, dimensions):
        torch.manual_seed(21)
        torch.set_num_threads(min(4, torch.get_num_threads()))
        cuda = torch.cuda.is_available()
        if settings.device == "GPU" and not cuda:
            raise ValueError("CUDA 사용 불가. Auto 또는 CPU를 선택하세요.")
        self.device = "cuda" if settings.device != "CPU" and cuda else "cpu"
        self.settings = settings
        self.batch = settings.batch
        self.gpu = ""
        if self.device == "cuda":
            info = torch.cuda.get_device_properties(0)
            self.gpu = info.name
            self.batch = min(self.batch, 16 if info.total_memory < 4 * 2**30 else 64)
        self.network = Network(name, dimensions, settings.sequence).to(self.device)

    def fit(self, x, y, val_x, val_y, progress, phase):
        tensor = lambda data: torch.as_tensor(data, dtype=torch.float32)
        loader = DataLoader(TensorDataset(tensor(x), tensor(y)), batch_size=self.batch, shuffle=True)
        val_loader = DataLoader(TensorDataset(tensor(val_x), tensor(val_y)), batch_size=self.batch)
        optimizer = torch.optim.AdamW(self.network.parameters(), lr=self.settings.lr)
        best, wait = float("inf"), 0
        state = None

        def run(batches, training):
            self.network.train(training)
            total = 0.0
            with torch.set_grad_enabled(training):
                for xb, yb in batches:
                    xb, yb = xb.to(self.device), yb.to(self.device)
                    logits, returns = self.network(xb)
                    loss = (nn.functional.binary_cross_entropy_with_logits(logits, (yb>0).float())
                            + nn.functional.smooth_l1_loss(returns, yb*10))
                    if training:
                        optimizer.zero_grad(set_to_none=True)
                        loss.backward()
                        nn.utils.clip_grad_norm_(self.network.parameters(), 1)
                        optimizer.step()
                    total += loss.item()*len(xb)
            return total / len(batches.dataset)

        for epoch in range(1, self.settings.epochs+1):
            train_loss, val_loss = run(loader, True), run(val_loader, False)
            memory = round(torch.cuda.max_memory_allocated()/2**20) if self.device == "cuda" else None
            progress({"phase": phase, "epoch": epoch, "epochs": self.settings.epochs,
                      "train_loss": train_loss, "val_loss": val_loss, "device": self.device,
                      "gpu": self.gpu, "gpu_mib": memory, "batch": self.batch})
            if val_loss < best:
                best, wait = val_loss, 0
                state = deepcopy(self.network.state_dict())
            else:
                wait += 1
                if wait >= self.settings.patience:
                    break
        if state is None:
            raise ValueError("학습 손실이 유한하지 않습니다.")
        self.network.load_state_dict(state)

    def predict(self, x):
        self.network.eval()
        probabilities, returns = [], []
        with torch.inference_mode():
            for chunk in np.array_split(x, math.ceil(len(x)/self.batch)):
                logits, estimate = self.network(torch.as_tensor(chunk, device=self.device, dtype=torch.float32))
                probabilities.append(logits.sigmoid().cpu().numpy())
                returns.append((estimate/10).cpu().numpy())
        return np.concatenate(probabilities), np.concatenate(returns)

    def save(self, path):
        from safetensors.torch import save_file
        save_file({k: v.cpu().contiguous() for k, v in self.network.state_dict().items()}, str(path))
