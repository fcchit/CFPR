from tqdm import tqdm
from torch.utils.data import DataLoader
import torch.nn as nn
import torch
from tensorboardX import SummaryWriter
from sklearn import metrics
import numpy as np
import yaml
import time
import os
import random
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.database import kitti_dataset
from modules.loss import triplet_loss
from modules.overlapnetvlad import vlad_head
from evaluate import evaluate

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
randg = np.random.RandomState()


def train(config):
    root = config["data_root"]["data_root_folder"]
    out_folder = config["training_config"]["out_folder"]
    training_seqs = config["training_config"]["training_seqs"]
    pretrained_vlad_model = config["training_config"]["pretrained_vlad_model"]
    pos_threshold = config["training_config"]["pos_threshold"]
    neg_threshold = config["training_config"]["neg_threshold"]
    batch_size = config["training_config"]["batch_size"]
    epochs = config["training_config"]["epoch"]

    os.makedirs(out_folder, exist_ok=True)

    writer = SummaryWriter()

    train_dataset = kitti_dataset(
        root=root,
        seqs=training_seqs,
        pos_threshold=pos_threshold,
        neg_threshold=neg_threshold
    )
    train_loader = DataLoader(
        dataset=train_dataset, batch_size=batch_size,
        shuffle=True, num_workers=0
    )

    vlad = vlad_head().to(device=device)
    optimizer = torch.optim.Adam(
        filter(lambda p: p.requires_grad, vlad.parameters()),
        lr=1e-5, weight_decay=1e-6
    )

    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
    loss_function = triplet_loss

    if pretrained_vlad_model:
        checkpoint = torch.load(pretrained_vlad_model)
        vlad.load_state_dict(checkpoint['state_dict'])

    step = 0
    for epoch in range(epochs):
        vlad.train()
        for i_batch, sample_batch in tqdm(enumerate(train_loader), total=len(train_loader), desc='Train epoch ' + str(epoch), leave=False):
            optimizer.zero_grad()

            input_data = torch.cat([
                sample_batch['query_desc'].flatten(0, 1),
                sample_batch['pos_desc'].flatten(0, 1),
                sample_batch['neg_desc'].flatten(0, 1),
            ], dim=0).to(device)

            out = vlad(input_data)

            if out.shape[0] != batch_size * 13:
                continue

            query_fea, pos_fea, neg_fea = torch.split(out, [batch_size, batch_size * 2, batch_size * 10], dim=0)

            query_fea = query_fea.unsqueeze(1)
            pos_fea = pos_fea.reshape(batch_size, 2, -1)
            neg_fea = neg_fea.reshape(batch_size, 10, -1)

            train_dataset.update_latent_vectors(query_fea, sample_batch['id'])

            loss = loss_function(query_fea, pos_fea, neg_fea, 0.3)
            # print(f"Loss: {loss.cpu().item()}")

            loss.backward()
            optimizer.step()

            with torch.no_grad():
                writer.add_scalar('loss', loss.cpu().item(), global_step=step)
                writer.add_scalar('LR', optimizer.state_dict()['param_groups'][0]['lr'], global_step=step)
                step += 1

            if epoch % 10 == 0:
                torch.save({
                    'epoch': epoch,
                    'state_dict': vlad.state_dict(),
                    'optimizer': optimizer.state_dict(),
                    'step': step
                }, os.path.join(out_folder, f"epoch_{epoch}.ckpt"))
                scheduler.step()

    writer.close()


if __name__ == '__main__':
    config = yaml.safe_load(open('./config/config.yml'))
    train(config)