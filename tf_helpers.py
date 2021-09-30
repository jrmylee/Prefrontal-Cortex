import tensorflow as tf
import librosa
import numpy as np
from librosa import mel_frequencies

class HParams(object):
    """ Hparams was removed from tf 2.0alpha so this is a placeholder
    """
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

def mel_spec(spectrogram, hparams):
    mel_matrix = tf.signal.linear_to_mel_weight_matrix(
        num_mel_bins=hparams.num_mel_bins,
        num_spectrogram_bins=int(hparams.n_fft/2)+1,
        sample_rate=22050,
        lower_edge_hertz=hparams.mel_lower_edge_hertz,
        upper_edge_hertz=hparams.mel_upper_edge_hertz,
        dtype=tf.dtypes.float32,
        name=None,
    )
    mel_f = mel_frequencies(
        n_mels=hparams.num_mel_bins + 2,
        fmin=hparams.mel_lower_edge_hertz,
        fmax=hparams.mel_upper_edge_hertz,
    )
    enorm = tf.dtypes.cast(
        tf.expand_dims(tf.constant(2.0 / (mel_f[2 : hparams.num_mel_bins + 2] - mel_f[:hparams.num_mel_bins])), 0),
        tf.float32)
    mel_matrix = tf.multiply(mel_matrix, enorm)
    mel_matrix = tf.divide(mel_matrix, tf.reduce_sum(mel_matrix, axis=0))
    mel_spectrogram = tf.tensordot(spectrogram,mel_matrix, 1)
    return mel_spectrogram

def inv_mel_spec(mel_spectrogram, hparams):
    mel_matrix = tf.signal.linear_to_mel_weight_matrix(
        num_mel_bins=hparams.num_mel_bins,
        num_spectrogram_bins=int(hparams.n_fft/2)+1,
        sample_rate=22050,
        lower_edge_hertz=hparams.mel_lower_edge_hertz,
        upper_edge_hertz=hparams.mel_upper_edge_hertz,
        dtype=tf.dtypes.float32,
        name=None,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        mel_inversion_matrix = tf.constant(
            np.nan_to_num(
                np.divide(mel_matrix.numpy().T, np.sum(mel_matrix.numpy(), axis=1))
            ).T
        )
    mel_spectrogram_inv = tf.tensordot(mel_spectrogram,tf.transpose(mel_inversion_matrix), 1)
    reconstructed_y_mel = inv_spectrogram_tensorflow(np.transpose(mel_spectrogram_inv), hparams)
    
    return reconstructed_y_mel

def _normalize_tensorflow(S, hparams):
    return tf.clip_by_value((S - hparams.min_level_db) / -hparams.min_level_db, 0, 1)

def _tf_log10(x):
    numerator = tf.math.log(x)
    denominator = tf.math.log(tf.constant(10, dtype=numerator.dtype))
    return numerator / denominator


def _amp_to_db_tensorflow(x):
    return 20 * _tf_log10(tf.clip_by_value(tf.abs(x), 1e-5, 1e100))


def _stft_tensorflow(signals, hparams):
    return tf.signal.stft(
        signals,
        hparams.win_length,
        hparams.hop_length,
        hparams.n_fft,
        pad_end=True,
        window_fn=tf.signal.hann_window,
    )

def spectrogram_tensorflow(y, hparams):
    D = _stft_tensorflow(y, hparams)
    S = _amp_to_db_tensorflow(tf.abs(D)) - hparams.ref_level_db
    return _normalize_tensorflow(S, hparams)

def _istft(y, hparams):
    return librosa.istft(
        y, hop_length=hparams.hop_length, win_length=hparams.win_length
    )


def _stft(y, hparams):
    return librosa.stft(
        y=y,
        n_fft=hparams.n_fft,
        hop_length=hparams.hop_length,
        win_length=hparams.win_length,
    )


def _db_to_amp_tensorflow(x):
    return tf.pow(tf.ones(tf.shape(x)) * 10.0, x * 0.05)


# use this one when istft is fixed!
def _istft_tensorflow(stfts, hparams):
    return tf.signal.inverse_stft(
        stfts, hparams.win_length, hparams.hop_length, hparams.n_fft
    )


def _denormalize_tensorflow(S, hparams):
    return (tf.clip_by_value(S, 0, 1) * -hparams.min_level_db) + hparams.min_level_db


def _griffin_lim_tensorflow(S, hparams, use_tf_istft=False):
    """TensorFlow implementation of Griffin-Lim
  Based on https://github.com/Kyubyong/tensorflow-exercises/blob/master/Audio_Processing.ipynb and
  https://github.com/keithito/tacotron/blob/master/util/audio.py
  issue: https://github.com/tensorflow/tensorflow/issues/28444
  """
    # TensorFlow's stft and istft operate on a batch of spectrograms; create batch of size 1
    pad_amount = 2 * (hparams.win_length - hparams.hop_length)
    if use_tf_istft:
        S = tf.expand_dims(S, 0)
        S_complex = tf.identity(tf.cast(S, dtype=tf.complex64))
        y = tf.py_function(_istft(S_complex, hparams))
        for i in range(hparams.griffin_lim_iters):
            est = _stft_tensorflow(y)
            angles = est / tf.cast(tf.maximum(1e-8, tf.abs(est)), tf.complex64)
            y = _istft_tensorflow(S_complex * angles, hparams)
        return tf.squeeze(y, 0)

    else:
        angles = np.exp(2j * np.pi * np.random.rand(*S.shape))
        S_complex = np.abs(S).astype(np.complex)
        y = _istft(S_complex * angles, hparams)
        for i in range(hparams.griffin_lim_iters):
            angles = np.exp(1j * np.angle(_stft(y, hparams)))
            y = _istft(S_complex * angles, hparams)
        return y


def inv_spectrogram_tensorflow(spectrogram, hparams):
    """Converts spectrogram to waveform using librosa"""
    S = _db_to_amp_tensorflow(
        _denormalize_tensorflow(spectrogram, hparams) + hparams.ref_level_db
    )  # Convert back to linear
    return _griffin_lim_tensorflow(S ** hparams.power, hparams)  # Reconstruct phase