import torch
import torch.nn as nn
import torch.nn.functional as F
import math

from utils import cosine_scheduler


class Loss(nn.Module):
    def __init__(self):
        super(Loss, self).__init__()

    def forward(self, y, **kwargs):
        raise NotImplementedError("TODO : ")


class ReconstructionLoss(Loss):
    def __init__(self):
        super(ReconstructionLoss, self).__init__()

    def forward(self, y, yhat):
        return F.l1_loss(y, yhat)


class GTCRLoss(Loss):
    def __init__(self, epsilon=1e-3):
        super(GTCRLoss, self).__init__()

        self.epsilon = epsilon

    def forward(self, z, scale_factor):
        B, D = z.size()
        lmbd = D / (B * self.epsilon)

        return (
            -(
                torch.logdet(((z.T.matmul(z) * lmbd) + torch.eye(D, device=z.device)))
                * 0.5
            )
            / scale_factor
        )


class RegLoss(Loss):
    def __init__(self, sigma=0.5):
        super(RegLoss, self).__init__()
        self.sigma = sigma

    def forward(self, u):
        return (0.5 - 0.5 * torch.erf(-(u + 0.5) / (math.sqrt(2) * self.sigma))).mean()


class SparseLoss(Loss):
    def __init__(self, epsilon=1e-3, pretrain=True):
        super(SparseLoss, self).__init__()

        self.epsilon = epsilon
        self.pretrain = pretrain

    def forward(
        self,
        X,
        X_hat,
        X_input_noised_hat,
        X_latent_noised_hat,
        X_z_hat=None,
        z=None,
        mu=None,
        lmbd=None,
        local_gates_lmbd=100,
    ):
        input_recons = ReconstructionLoss()
        input_denoising_recons = ReconstructionLoss()
        latent_denoising_recons = ReconstructionLoss()
        gate_recons = ReconstructionLoss()

        if self.pretrain:
            gtcr_reg = 0
            X_z_hat = X
        else:
            gtcr = GTCRLoss(self.epsilon)
            reg = RegLoss(sigma=0.5)
            gtcr_reg = gtcr(z, X.size(0)) + lmbd * reg(mu)

        return (
            input_recons(X, X_hat)
            + local_gates_lmbd * gate_recons(X, X_z_hat)
            + local_gates_lmbd * input_denoising_recons(X, X_input_noised_hat)
            + latent_denoising_recons(X, X_latent_noised_hat)
            + gtcr_reg
        )


class HeadLoss(Loss):
    def __init__(self, nb_classes):
        super(HeadLoss, self).__init__()
        self.nb_classes = nb_classes

    def forward(self, h, yhat):
        h = h.T
        D, B = h.size()

        I = torch.eye(D, device=h.device).expand((self.nb_classes, D, D))

        assign = yhat.T.reshape((self.nb_classes, 1, -1))
        intense_clust = assign.sum(2) + 1e-8
        scale = (D / (intense_clust * 1e-1)).view(self.nb_classes, 1, 1)
        h = h.view((1, D, B))

        log_det = torch.logdet(I + scale * h.mul(assign).matmul(h.transpose(1, 2)))
        compress_loss = (intense_clust.squeeze() * log_det / (2 * B)).sum()
        return compress_loss / self.nb_classes


class ClusterLoss(Loss):
    def __init__(self, nb_classes, epsilon=1e-3, pretrain=True):
        super(ClusterLoss, self).__init__()

        self.nb_classes = nb_classes
        self.pretrain = pretrain
        self.epsilon = epsilon

    def forward(self, h, yhat, yg, u_zg, lmbd, gamma):
        head = HeadLoss(self.nb_classes)
        reg = RegLoss(sigma=0.5)
        gtcr = GTCRLoss(self.epsilon)

        yhat_argmax = yhat.argmax(dim=1)

        if self.pretrain:
            return gamma * head(h, yhat) + gtcr(h, self.nb_classes)

        return gamma * head(h, yhat) + gtcr(h, self.nb_classes), F.cross_entropy(
            yg, yhat_argmax
        ) + lmbd * reg(u_zg)
