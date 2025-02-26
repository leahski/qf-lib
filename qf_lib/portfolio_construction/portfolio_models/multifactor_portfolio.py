#     Copyright 2016-present CERN – European Organization for Nuclear Research
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.

from typing import Union, Sequence

import numpy as np

from qf_lib.containers.dataframe.qf_dataframe import QFDataFrame
from qf_lib.containers.series.qf_series import QFSeries
from qf_lib.portfolio_construction.optimizers.quadratic_optimizer import QuadraticOptimizer
from qf_lib.portfolio_construction.portfolio_models.portfolio import Portfolio


class PortfolioParameters:
    def __init__(self, min_port_vol_weight, max_mean_ret_weight, min_max_dd_weight, max_skewness_weight,
                 max_up_vol_weight):
        self.min_port_vol_weight = min_port_vol_weight
        self.max_mean_ret_weight = max_mean_ret_weight
        self.min_max_dd_weight = min_max_dd_weight
        self.max_skewness_weight = max_skewness_weight
        self.max_up_vol_weight = max_up_vol_weight


class MultiFactorPortfolio(Portfolio):
    """
    Class used for constructing a portfolio. It optimizes a portfolio considering:

    - variance of a portfolio(minimizing),
    - mean return of portfolio's assets (maximizing),
    - max drawdown of the portfolio (minimizing).
    """

    def __init__(self, covariance_matrix: QFDataFrame, mean_return: QFSeries, max_dd: QFSeries,
                 skewness: QFSeries, up_vol: QFSeries,
                 parameters: PortfolioParameters, upper_constraint: Union[float, Sequence[float]] = None):
        self.covariance_matrix = covariance_matrix
        self.mean_return = mean_return
        self.max_dd = max_dd
        self.skewness = skewness
        self.upside_vol = up_vol

        self.parameters = parameters
        self.upper_constraint = upper_constraint

        self._normalize_vectors()

    def _normalize_vectors(self):
        # apply sqrt transform to some of the factors to make their distribution closer to normal
        self.mean_return = self.mean_return.min_max_normalized()
        self.max_dd = np.sqrt(self.max_dd.min_max_normalized())  # sqrt
        self.skewness = self.skewness.min_max_normalized()
        self.upside_vol = np.sqrt(self.upside_vol.min_max_normalized())  # sqrt

    def get_weights(self) -> QFSeries:
        x = self._get_equal_weights()

        covariance_value = x.transpose() @ self.covariance_matrix @ x
        covariance_impact = 0.5 * covariance_value  # 0.5 stands for 1/2 in minimize (1/2)*x'*P*x + q'*x

        mean_ret_value = x @ self.mean_return
        max_dd_value = x @ self.max_dd
        skewness_value = x @ self.skewness
        upside_vol_value = x @ self.upside_vol

        # calculate normalising weights, weights that will make all factors of equal importance for the optimizer
        mean_ret_norm_wt = covariance_impact / mean_ret_value
        max_dd_norm_wt = covariance_impact / max_dd_value
        skewness_norm_wt = covariance_impact / skewness_value
        up_vol_norm_wt = covariance_impact / upside_vol_value

        mean_ret_part = mean_ret_norm_wt * self.parameters.max_mean_ret_weight * self.mean_return * (-1)
        min_max_dd_part = max_dd_norm_wt * self.parameters.min_max_dd_weight * self.max_dd
        skewness_part = skewness_norm_wt * self.parameters.max_skewness_weight * self.skewness * (-1)
        up_vol_part = up_vol_norm_wt * self.parameters.max_up_vol_weight * self.upside_vol * (-1)

        # combine all factors together
        q = mean_ret_part + min_max_dd_part + skewness_part + up_vol_part
        P = self.parameters.min_port_vol_weight * self.covariance_matrix

        # run optimisation
        weights = QuadraticOptimizer.get_optimal_weights(P.values, q.values, upper_constraints=self.upper_constraint)
        stocks_weights = QFSeries(data=weights, index=P.columns)
        return stocks_weights

    def _get_equal_weights(self):
        n = self.mean_return.size
        x = self.mean_return.copy()
        x[:] = 1/n
        return x
