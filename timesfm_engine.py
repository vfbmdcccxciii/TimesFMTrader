"""
TimesFM 2.5 (200M PyTorch) wrapper.

Loads once at construction. Forecasts a single univariate series and
returns point forecast + 10th/90th percentile quantile bands.
"""

import logging
import numpy as np
import timesfm

log = logging.getLogger(__name__)


# TimesFM 2.5 requires max_horizon to be a multiple of the output patch size (128).
# We always forecast a full 128-step block and slice down to the user's horizon.
_OUTPUT_PATCH = 128


class TimesFMForecaster:
    def __init__(self, horizon: int = 5, max_context: int = 256):
        if horizon < 1 or horizon > _OUTPUT_PATCH:
            raise ValueError(f"horizon must be 1..{_OUTPUT_PATCH}, got {horizon}")
        self.horizon = horizon
        log.info("Loading google/timesfm-2.5-200m-pytorch ...")
        self.model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch"
        )
        self.model.compile(
            timesfm.ForecastConfig(
                max_context=max_context,
                max_horizon=_OUTPUT_PATCH,            # must be multiple of 128
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,               # prices are positive
                fix_quantile_crossing=True,
            )
        )

    def forecast(self, series: np.ndarray):
        """
        series: 1D numpy array of close prices (oldest -> newest).
        Returns (point[H], q10[H], q90[H]) for H = self.horizon.
        Internally forecasts 128 steps and slices to self.horizon.
        """
        series = np.asarray(series, dtype=np.float32)
        point_forecast, quantile_forecast = self.model.forecast(
            horizon=_OUTPUT_PATCH,
            inputs=[series],
        )
        # quantile_forecast shape: (B, 128, 10) -> mean + q10..q90
        # index 1 = q10, index 9 = q90. Slice to self.horizon.
        point = point_forecast[0, :self.horizon]              # (H,)
        q10   = quantile_forecast[0, :self.horizon, 1]        # (H,)
        q90   = quantile_forecast[0, :self.horizon, 9]        # (H,)
        return point, q10, q90
