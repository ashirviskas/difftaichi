# To run on CPU:
#   CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 python3 smoke_pytorch.py

import os
import time

import matplotlib
import torch
# from torch import nn
import torchvision.models as models
from imageio import imread

device = torch.device("cuda") if torch.cuda.is_available() else torch.device(
    "cpu")

num_iterations = 105
n_grid = 110
dx = 1.0 / n_grid
steps = 100
learning_rate = 165
lr_decay = 0.985


def roll_col(t, n):
    return torch.cat((t[:, -n:], t[:, :-n]), axis=1)


def roll_row(t, n):
    return torch.cat((t[-n:, :], t[:-n, :]), axis=0)


def project(vx, vy):
    """Project the velocity field to be approximately mass-conserving,
       using a few iterations of Gauss-Seidel."""
    p = torch.zeros(vx.shape).to(device)
    h = 1.0 / vx.shape[0]
    div = -0.5 * h * (
            roll_row(vx, -1) - roll_row(vx, 1) + roll_col(vy, -1) - roll_col(vy, 1))

    for k in range(6):
        p = (div + roll_row(p, 1) + roll_row(p, -1) + roll_col(p, 1) + roll_col(
            p, -1)) / 4.0

    vx -= 0.5 * (roll_row(p, -1) - roll_row(p, 1)) / h
    vy -= 0.5 * (roll_col(p, -1) - roll_col(p, 1)) / h
    return vx, vy


def advect(f, vx, vy):
    """Move field f according to x and y velocities (u and v)
       using an implicit Euler integrator."""
    rows, cols = f.shape
    cell_ys, cell_xs = torch.meshgrid(torch.arange(rows), torch.arange(cols))
    cell_ys = torch.transpose(cell_ys, 0, 1).float().to(device)
    cell_xs = torch.transpose(cell_xs, 0, 1).float().to(device)
    center_xs = (cell_xs - vx).flatten()
    center_ys = (cell_ys - vy).flatten()

    # Compute indices of source cells.
    left_ix = torch.floor(center_xs).long()
    top_ix = torch.floor(center_ys).long()
    rw = center_xs - left_ix.float()  # Relative weight of right-hand cells.
    bw = center_ys - top_ix.float()  # Relative weight of bottom cells.
    left_ix = torch.remainder(left_ix, rows)  # Wrap around edges of simulation.
    right_ix = torch.remainder(left_ix + 1, rows)
    top_ix = torch.remainder(top_ix, cols)
    bot_ix = torch.remainder(top_ix + 1, cols)

    # A linearly-weighted sum of the 4 surrounding cells.
    flat_f = (1 - rw) * ((1 - bw) * f[left_ix, top_ix] + bw * f[left_ix, bot_ix]) \
             + rw * ((1 - bw) * f[right_ix, top_ix] + bw * f[right_ix, bot_ix])
    return torch.reshape(flat_f, (rows, cols))


def forward(iteration, smoke, vx, vy, output):
    for t in range(1, steps):
        vx_updated = advect(vx, vx, vy)
        vy_updated = advect(vy, vx, vy)
        vx, vy = project(vx_updated, vy_updated)
        smoke = advect(smoke, vx, vy)

        if output:
            matplotlib.image.imsave("output_pytorch/step{0:03d}.png".format(t),
                                    255 * smoke.cpu().detach().numpy(), cmap='gray')

    return smoke


def reshape_for_mobilenet(target):
    target_activations = target[None, :, :]
    target_activations = torch.cat((target_activations, target_activations, target_activations), 0)
    target_activations = target_activations[None, :, :, :]
    return target_activations


def main(learning_rate):
    os.system("mkdir -p output_pytorch")
    print("Loading initial and target states...")
    initial_smoke_img = imread("init_smoke_2.png")[:, :, 0] / 255.0
    target_img = imread("keksas.png")[:, :] / 255.0

    vx = torch.zeros(
        n_grid, n_grid, requires_grad=True, device=device, dtype=torch.float32)
    vy = torch.zeros(
        n_grid, n_grid, requires_grad=True, device=device, dtype=torch.float32)
    initial_smoke = torch.tensor(
        initial_smoke_img, device=device, dtype=torch.float32)
    target = torch.tensor(target_img, device=device, dtype=torch.float32)

    mobilenet = models.mobilenet_v2(pretrained=True)
    target_activations = reshape_for_mobilenet(target)

    for param in mobilenet.parameters():
        param.requires_grad = False

    modulelist = list(mobilenet.features.modules())[0]
    target_activations = [target_activations]
    for l in modulelist[:2]:
        print(l)
        target_activations.append(l(target_activations[-1]))

    for opt in range(num_iterations):
        learning_rate *= lr_decay
        t = time.time()
        smoke = forward(opt, initial_smoke, vx, vy, opt == (num_iterations - 1))

        smoke_activations = reshape_for_mobilenet(smoke)
        smoke_activations = [smoke_activations]

        for l in modulelist[:2]:
            smoke_activations.append(l(smoke_activations[-1]))

        loss = ((smoke - target) ** 2).mean()
        loss += ((smoke_activations[0] - target_activations[0]) ** 2).mean() * 0.1
        loss += ((smoke_activations[1] - target_activations[1]) ** 2).mean() * 0.1

        print('forward time', (time.time() - t) * 1000, 'ms')

        t = time.time()
        loss.backward()
        print('backward time', (time.time() - t) * 1000, 'ms')
        # learning_rate = learning_rate * 0.98
        print(learning_rate)
        with torch.no_grad():
            vx -= learning_rate * vx.grad.data
            vy -= learning_rate * vy.grad.data
            vx.grad.data.zero_()
            vy.grad.data.zero_()

        print('Iter', opt, ' Loss =', loss.item())


if __name__ == '__main__':
    main(learning_rate)
