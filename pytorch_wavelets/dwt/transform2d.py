import torch.nn as nn
import pywt
import pytorch_wavelets.dwt.lowlevel as lowlevel
import torch
torch.autograd.set_detect_anomaly(True)


class DWTForward(nn.Module):
    """ Performs a 2d DWT Forward decomposition of an image

    Args:
        J (int): Number of levels of decomposition
        wave (str or pywt.Wavelet): Which wavelet to use. Can be a string to
            pass to pywt.Wavelet constructor, can also be a pywt.Wavelet class,
            or can be a two tuple of array-like objects for the analysis low and
            high pass filters.
        mode (str): 'zero', 'symmetric', 'reflect' or 'periodization'. The
            padding scheme
        separable (bool): whether to do the filtering separably or not (the
            naive implementation can be faster on a gpu).
        """
    def __init__(self, J=1, wave='db1', mode='zero'):
        super().__init__()
        if isinstance(wave, str):
            wave = pywt.Wavelet(wave)
        if isinstance(wave, pywt.Wavelet):
            h0_col, h1_col = wave.dec_lo, wave.dec_hi
            h0_row, h1_row = h0_col, h1_col
        else:
            if len(wave) == 2:
                h0_col, h1_col = wave[0], wave[1]
                h0_row, h1_row = h0_col, h1_col
            elif len(wave) == 4:
                h0_col, h1_col = wave[0], wave[1]
                h0_row, h1_row = wave[2], wave[3]

        # Prepare the filters
        filts = lowlevel.prep_filt_afb2d(h0_col, h1_col, h0_row, h1_row)
        self.register_buffer('h0_col', filts[0])
        self.register_buffer('h1_col', filts[1])
        self.register_buffer('h0_row', filts[2])
        self.register_buffer('h1_row', filts[3])
        self.J = J
        self.mode = mode

    def forward(self, x):
        """ Forward pass of the DWT.

        Args:
            x (tensor): Input of shape :math:`(N, C_{in}, H_{in}, W_{in})`

        Returns:
            (yl, yh)
                tuple of lowpass (yl) and bandpass (yh)
                coefficients. yh is a list of length J with the first entry
                being the finest scale coefficients. yl has shape
                :math:`(N, C_{in}, H_{in}', W_{in}')` and yh has shape
                :math:`list(N, C_{in}, 3, H_{in}'', W_{in}'')`. The new
                dimension in yh iterates over the LH, HL and HH coefficients.

        Note:
            :math:`H_{in}', W_{in}', H_{in}'', W_{in}''` denote the correctly
            downsampled shapes of the DWT pyramid.
        """
        yh = []
        ll = x
        mode = lowlevel.mode_to_int(self.mode)

        # Do a multilevel transform
        for j in range(self.J):
            # Do 1 level of the transform
            ll_cur, high = lowlevel.AFB2D.apply(ll, self.h0_col.clone(), self.h1_col.clone(), self.h0_row.clone(), self.h1_row.clone(), mode)
            yh.append(high)
            ll = ll_cur

        return ll, yh


class DWTInverse(nn.Module):
    """ Performs a 2d DWT Inverse reconstruction of an image

    Args:
        wave (str or pywt.Wavelet): Which wavelet to use
        C: deprecated, will be removed in future
    """
    def __init__(self, wave='db1', mode='zero'):
        super().__init__()
        if isinstance(wave, str):
            wave = pywt.Wavelet(wave)
        if isinstance(wave, pywt.Wavelet):
            g0_col, g1_col = wave.rec_lo, wave.rec_hi
            g0_row, g1_row = g0_col, g1_col
        else:
            if len(wave) == 2:
                g0_col, g1_col = wave[0], wave[1]
                g0_row, g1_row = g0_col, g1_col
            elif len(wave) == 4:
                g0_col, g1_col = wave[0], wave[1]
                g0_row, g1_row = wave[2], wave[3]
        # Prepare the filters
        filts = lowlevel.prep_filt_sfb2d(g0_col, g1_col, g0_row, g1_row)
        self.register_buffer('g0_col', filts[0])
        self.register_buffer('g1_col', filts[1])
        self.register_buffer('g0_row', filts[2])
        self.register_buffer('g1_row', filts[3])
        self.mode = mode

    def forward(self, coeffs):
        """
        Args:
            coeffs (yl, yh): tuple of lowpass and bandpass coefficients, where:
              yl is a lowpass tensor of shape :math:`(N, C_{in}, H_{in}',
              W_{in}')` and yh is a list of bandpass tensors of shape
              :math:`list(N, C_{in}, 3, H_{in}'', W_{in}'')`. I.e. should match
              the format returned by DWTForward

        Returns:
            Reconstructed input of shape :math:`(N, C_{in}, H_{in}, W_{in})`

        Note:
            :math:`H_{in}', W_{in}', H_{in}'', W_{in}''` denote the correctly
            downsampled shapes of the DWT pyramid.

        Note:
            Can have None for any of the highpass scales and will treat the
            values as zeros (not in an efficient way though).
        """
        yl, yh = coeffs
        ll_prev = yl
        mode = lowlevel.mode_to_int(self.mode)

        # Do a multilevel inverse transform
        for h in yh[::-1]:
            if h is None:
                h = torch.zeros(ll_prev.shape[0], ll_prev.shape[1], 3, ll_prev.shape[-2],
                                ll_prev.shape[-1], device=ll_prev.device)

            # 'Unpad' added dimensions
            if ll_prev.shape[-2] > h.shape[-2]:
                ll_prev = ll_prev[..., :-1, :].clone()
            if ll_prev.shape[-1] > h.shape[-1]:
                ll_prev = ll_prev[..., :-1].clone()
            print("ll_prev size", ll_prev.size())
            print("h size", h.size())
            print("self.g0_col size", self.g0_col.size())
            print("self.g1_col size", self.g1_col.size())
            print("self.g0_row size", self.g0_row.size())
            print("self.g1_row size", self.g1_row.size())
            ll_cur = lowlevel.SFB2D.apply(ll_prev, h, self.g0_col.clone(), self.g1_col.clone(), self.g0_row.clone(), self.g1_row.clone(), mode)
            ll_prev = ll_cur
        return ll_prev


class SWTForward(nn.Module):
    """ Performs a 2d Stationary wavelet transform (or undecimated wavelet
    transform) of an image

    Args:
        J (int): Number of levels of decomposition
        wave (str or pywt.Wavelet): Which wavelet to use. Can be a string to
            pass to pywt.Wavelet constructor, can also be a pywt.Wavelet class,
            or can be a two tuple of array-like objects for the analysis low and
            high pass filters.
        mode (str): 'zero', 'symmetric', 'reflect' or 'periodization'. The
            padding scheme. PyWavelets uses only periodization so we use this
            as our default scheme.
        """
    def __init__(self, J=1, wave='db1', mode='periodization'):
        super().__init__()
        if isinstance(wave, str):
            wave = pywt.Wavelet(wave)
        if isinstance(wave, pywt.Wavelet):
            h0_col, h1_col = wave.dec_lo, wave.dec_hi
            h0_row, h1_row = h0_col, h1_col
        else:
            if len(wave) == 2:
                h0_col, h1_col = wave[0], wave[1]
                h0_row, h1_row = h0_col, h1_col
            elif len(wave) == 4:
                h0_col, h1_col = wave[0], wave[1]
                h0_row, h1_row = wave[2], wave[3]

        # Prepare the filters
        filts = lowlevel.prep_filt_afb2d(h0_col, h1_col, h0_row, h1_row)
        self.register_buffer('h0_col', filts[0])
        self.register_buffer('h1_col', filts[1])
        self.register_buffer('h0_row', filts[2])
        self.register_buffer('h1_row', filts[3])

        self.J = J
        self.mode = mode

    def forward(self, x):
        """ Forward pass of the SWT.

        Args:
            x (tensor): Input of shape :math:`(N, C_{in}, H_{in}, W_{in})`

        Returns:
            List of coefficients for each scale. Each coefficient has
            shape :math:`(N, C_{in}, 4, H_{in}, W_{in})` where the extra
            dimension stores the 4 subbands for each scale. The ordering in
            these 4 coefficients is: (A, H, V, D) or (ll, lh, hl, hh).
        """
        ll = x
        coeffs = []
        # Do a multilevel transform
        filts = (self.h0_col, self.h1_col, self.h0_row, self.h1_row)
        for j in range(self.J):
            # Do 1 level of the transform
            y = lowlevel.afb2d_atrous(ll, filts, self.mode, 2**j)
            coeffs.append(y)
            ll = y[:, :, 0].clone()

        return coeffs
