import pytest

import numpy as np
from Transform2d_np import Transform2d as Transform2d_np
from pytorch_wavelets import DTCWTForward, DTCWTInverse
import datasets
import torch
import py3nvml
PRECISION_DECIMAL = 3

HAVE_GPU = torch.cuda.is_available()
pytestmark = pytest.mark.skipif(HAVE_GPU == False, reason='Need a gpu to test cuda')


def setup():
    global barbara, barbara_t
    global bshape, bshape_half
    global ch
    py3nvml.grab_gpus(1, gpu_fraction=0.5)
    barbara = datasets.barbara()
    barbara = (barbara/barbara.max()).astype('float32')
    barbara = barbara.transpose([2, 0, 1])
    bshape = list(barbara.shape)
    bshape_half = bshape[:]
    bshape_half[1] //= 2
    barbara_t = torch.unsqueeze(torch.tensor(barbara, dtype=torch.float32),
                                dim=0).cuda()
    ch = barbara_t.shape[1]


def test_barbara_loaded():
    assert barbara.shape == (3, 512, 512)
    assert barbara.min() >= 0
    assert barbara.max() <= 1
    assert barbara.dtype == np.float32
    assert list(barbara_t.shape) == [1, 3, 512, 512]


def test_simple():
    xfm = DTCWTForward(J=3).cuda()
    Yl, Yh = xfm(barbara_t)
    assert len(Yl.shape) == 4
    assert len(Yh) == 3
    assert Yh[0].shape[-1] == 2


def test_specific_wavelet():
    xfm = DTCWTForward(J=3, biort='antonini', qshift='qshift_06').cuda()
    Yl, Yh = xfm(barbara_t)
    assert len(Yl.shape) == 4
    assert len(Yh) == 3
    assert Yh[0].shape[-1] == 2


def test_odd_rows():
    xfm = DTCWTForward(J=3).cuda()
    Yl, Yh = xfm(barbara_t[:,:,:509])


def test_odd_cols():
    xfm = DTCWTForward(J=3).cuda()
    Yl, Yh = xfm(barbara_t[:,:,:,:509])


def test_odd_rows_and_cols():
    xfm = DTCWTForward(J=3).cuda()
    Yl, Yh = xfm(barbara_t[:,:,:509,:509])


@pytest.mark.parametrize("J", [1,2,3,4,5])
def test_fwd(J):
    X = 100*np.random.randn(3, 5, 100, 100)
    xfm = DTCWTForward(J=J).cuda()
    Yl, Yh = xfm(torch.tensor(X, dtype=torch.float32).cuda())
    f1 = Transform2d_np()
    yl, yh = f1.forward(X, nlevels=J)

    np.testing.assert_array_almost_equal(
        Yl.cpu(), yl, decimal=PRECISION_DECIMAL)
    for i in range(len(yh)):
        np.testing.assert_array_almost_equal(
            Yh[i][...,0].cpu(), yh[i].real, decimal=PRECISION_DECIMAL)
        np.testing.assert_array_almost_equal(
            Yh[i][...,1].cpu(), yh[i].imag, decimal=PRECISION_DECIMAL)


@pytest.mark.parametrize("J", [1,2,3,4,5])
def test_fwd_skip_hps(J):
    X = 100*np.random.randn(3, 5, 100, 100)
    # Randomly turn on/off the highpass outputs
    hps = np.random.binomial(size=J, n=1,p=0.5).astype('bool')
    xfm = DTCWTForward(J=J, skip_hps=hps).cuda()
    Yl, Yh = xfm(torch.tensor(X, dtype=torch.float32).cuda())
    f1 = Transform2d_np()
    yl, yh = f1.forward(X, nlevels=J)

    np.testing.assert_array_almost_equal(
        Yl.cpu(), yl, decimal=PRECISION_DECIMAL)
    for j in range(J):
        if hps[j]:
            assert Yh[j].shape == torch.Size([0])
        else:
            np.testing.assert_array_almost_equal(
                Yh[j][...,0].cpu(), yh[j].real, decimal=PRECISION_DECIMAL)
            np.testing.assert_array_almost_equal(
                Yh[j][...,1].cpu(), yh[j].imag, decimal=PRECISION_DECIMAL)


@pytest.mark.parametrize("J", [1,2,3,4,5])
def test_inv(J):
    Yl = 100*np.random.randn(3, 5, 64, 64)
    Yhr = [np.random.randn(3, 5, 6, 2**j, 2**j) for j in range(4+J,4,-1)]
    Yhi = [np.random.randn(3, 5, 6, 2**j, 2**j) for j in range(4+J,4,-1)]
    Yh1 = [yhr + 1j*yhi for yhr, yhi in zip(Yhr, Yhi)]
    Yh2 = [torch.tensor(np.stack((yhr, yhi), axis=-1), dtype=torch.float32).cuda()
           for yhr, yhi in zip(Yhr, Yhi)]

    ifm = DTCWTInverse(J=J).cuda()
    X = ifm((torch.tensor(Yl, dtype=torch.float32).cuda(), Yh2))
    f1 = Transform2d_np()
    x = f1.inverse(Yl, Yh1)

    np.testing.assert_array_almost_equal(
        X.cpu(), x, decimal=PRECISION_DECIMAL)


@pytest.mark.parametrize("J", [1,2,3,4,5])
def test_inv_skip_hps(J):
    hps = np.random.binomial(size=J, n=1,p=0.5).astype('bool')
    Yl = 100*np.random.randn(3, 5, 64, 64)
    Yhr = [np.random.randn(3, 5, 6, 2**j, 2**j) for j in range(4+J,4,-1)]
    Yhi = [np.random.randn(3, 5, 6, 2**j, 2**j) for j in range(4+J,4,-1)]
    Yh1 = [yhr + 1j*yhi for yhr, yhi in zip(Yhr, Yhi)]
    Yh2 = [torch.tensor(np.stack((yhr, yhi), axis=-1),
                        dtype=torch.float32).cuda()
           for yhr, yhi in zip(Yhr, Yhi)]
    for j in range(J):
        if hps[j]:
            Yh2[j] = torch.tensor([])
            Yh1[j] = np.zeros_like(Yh1[j])

    ifm = DTCWTInverse(J=J).cuda()
    X = ifm((torch.tensor(Yl, dtype=torch.float32).cuda(), Yh2))
    # Also test giving None instead of an empty tensor
    for j in range(J):
        if hps[j]:
            Yh2[j] = None
    X2 = ifm((torch.tensor(Yl, dtype=torch.float32).cuda(), Yh2))
    f1 = Transform2d_np()
    x = f1.inverse(Yl, Yh1)

    np.testing.assert_array_almost_equal(
        X.cpu(), x, decimal=PRECISION_DECIMAL)
    np.testing.assert_array_almost_equal(
        X2.cpu(), x, decimal=PRECISION_DECIMAL)


# Test end to end with numpy inputs
@pytest.mark.parametrize("biort,qshift,size,J", [
    ('antonini','qshift_a', (128,128), 3),
    ('antonini','qshift_a', (126,126), 3),
    ('legall','qshift_a', (99,100), 4),
    ('near_sym_a','qshift_c', (104, 101), 2),
    ('near_sym_b','qshift_d', (126, 126), 3),
])
def test_end2end(biort, qshift, size, J):
    im = np.random.randn(5,6,*size).astype('float32')
    xfm = DTCWTForward(J=J).cuda()
    Yl, Yh = xfm(torch.tensor(im, dtype=torch.float32).cuda())
    ifm = DTCWTInverse(J=J).cuda()
    y = ifm((Yl, Yh))

    # Compare with numpy results
    f_np = Transform2d_np(biort=biort,qshift=qshift)
    yl, yh = f_np.forward(im, nlevels=J)
    y2 = f_np.inverse(yl, yh)

    np.testing.assert_array_almost_equal(y.cpu(), y2, decimal=PRECISION_DECIMAL)
