import gc
import torch

gc.collect()
torch.cuda.empty_cache()
print(torch.cuda.memory_summary())