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
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from numpy.polynomial.polynomial import polyfit

from qf_lib.analysis.backtests_overfitting.minimum_backtest_length import minBTL

try:
    from scipy.optimize import curve_fit
    from scipy.stats import stats
    is_scipy_installed = True
except ImportError:
    is_scipy_installed = False


from qf_lib.analysis.common.abstract_document import AbstractDocument
from qf_lib.common.enums.frequency import Frequency
from qf_lib.common.utils.error_handling import ErrorHandling
from qf_lib.common.utils.logging.qf_parent_logger import qf_logger
from qf_lib.common.utils.miscellaneous.constants import DAYS_PER_YEAR_AVG
from qf_lib.common.utils.ratios.omega_ratio import omega_ratio
from qf_lib.common.utils.ratios.sharpe_ratio import sharpe_ratio
from qf_lib.common.utils.ratios.sorino_ratio import sorino_ratio
from qf_lib.containers.dataframe.prices_dataframe import PricesDataFrame
from qf_lib.containers.dataframe.qf_dataframe import QFDataFrame
from qf_lib.containers.dataframe.simple_returns_dataframe import SimpleReturnsDataFrame
from qf_lib.containers.series.qf_series import QFSeries
from qf_lib.documents_utils.document_exporting.element.chart import ChartElement
from qf_lib.documents_utils.document_exporting.element.df_table import DFTable
from qf_lib.documents_utils.document_exporting.element.heading import HeadingElement
from qf_lib.documents_utils.document_exporting.element.new_page import NewPageElement
from qf_lib.documents_utils.document_exporting.element.paragraph import ParagraphElement
from qf_lib.documents_utils.document_exporting.pdf_exporter import PDFExporter
from qf_lib.plotting.charts.chart import Chart
from qf_lib.plotting.charts.histogram_chart import HistogramChart
from qf_lib.plotting.charts.line_chart import LineChart
from qf_lib.plotting.decorators.axes_label_decorator import AxesLabelDecorator
from qf_lib.plotting.decorators.coordinate import DataCoordinate
from qf_lib.plotting.decorators.data_element_decorator import DataElementDecorator
from qf_lib.plotting.decorators.fill_between_decorator import FillBetweenDecorator
from qf_lib.plotting.decorators.legend_decorator import LegendDecorator
from qf_lib.plotting.decorators.scatter_decorator import ScatterDecorator
from qf_lib.plotting.decorators.text_decorator import TextDecorator
from qf_lib.plotting.decorators.title_decorator import TitleDecorator
from qf_lib.settings import Settings

from qf_lib.analysis.backtests_overfitting.overfitting_analysis import OverfittingAnalysis


@ErrorHandling.class_error_logging()
class BacktestOverfittingSheet(AbstractDocument):
    """
    Creates a document summarising all the statistics related to the probability of backtest overfitting.

    Parameters
    -----------
    settings: Settings
        necessary settings
    pdf_exporter: PDFExporter
        used to export the document to PDF
    title: str
        title of the document
    """

    def __init__(self, settings: Settings, pdf_exporter: PDFExporter, title: str = "Backtest overfitting"):
        super().__init__(settings, pdf_exporter, title)

        if not is_scipy_installed:
            warnings.warn(
                "Oops! It looks like 'scipy' is missing. To unlock the full capabilities of this library,"
                " install the extra dependencies with:\n"
                "    pip install -e .[detailed_analysis]",
                UserWarning
            )
            exit(1)

        self.ranking_functions = {
            "Sharpe Ratio": lambda series: sharpe_ratio(series, Frequency.DAILY),
            "Sortino Ratio": lambda series: sorino_ratio(series, Frequency.DAILY),
            "Omega Ratio": omega_ratio,
            "Total return": lambda series: series.to_prices().total_cumulative_return()
        }

        self.overfitting_analysis = {}  # type: Dict[str, OverfittingAnalysis]
        self.number_of_strategies = 0  # type: int
        self.backtests_length = 0.0  # type: float
        self.num_of_slices = 0  # type: int

        self.logger = qf_logger.getChild(self.__class__.__name__)

    def setup_overfitting_analysis(self, top_dir_path: Optional[str] = None,
                                   strategies_returns: Optional[SimpleReturnsDataFrame] = None,
                                   num_of_slices: int = 8):
        """
        Performs the overfitting analysis given either the path to the top directory, which contains the Timeseries
        excel files (in its subdirectories) generated by the backtest monitor, or the data frame containing the
        strategies returns.

        Parameters
        -----------
        top_dir_path: Optional[str]
            path to the top directory, which contains all the subdirectories with backtests results
        strategies_returns: Optional[SimpleReturnsDataFrame]
            dataframe containing simple returns of all strategies. The data frame index should contain the timestamps
            of the strategy returns in order to be able to compute all necessary ratios. Each column of the data frame
            should contain the simple returns of each of variants of the strategy / each of strategies.
        num_of_slices: int
            number of slices used in the overfitting analysis
        """
        if (top_dir_path is None) == (strategies_returns is None):
            raise ValueError("In order to complete the analysis you need to either provide the path to the top "
                             "directory with Timeseries files or the data frame of returns. Please provide exactly "
                             "one of these parameters.")

        if strategies_returns is None:
            strategies_returns = self.get_timeseries_returns(top_dir=top_dir_path)

        for function_name, ranking_function in self.ranking_functions.items():
            self.overfitting_analysis[function_name] = OverfittingAnalysis(strategies_returns,
                                                                           ranking_function=ranking_function,
                                                                           num_of_slices=num_of_slices)

        self.number_of_strategies = strategies_returns.num_of_columns
        backtest_start_date = strategies_returns.index[0]
        backtest_end_date = strategies_returns.index[-1]
        self.backtests_length = (backtest_end_date - backtest_start_date).days / DAYS_PER_YEAR_AVG
        self.num_of_slices = num_of_slices

    def build_document(self):
        self._add_header()
        self.document.add_element(ParagraphElement("\n"))

        self._add_minimum_backtest_length_plot()
        self._add_probability_of_backtest_overiftting_analysis()

        self._add_best_is_and_oos_qualities()
        self._add_logits_distribution_chart()
        self._add_stochastic_dominance_chart()

    def get_timeseries_returns(self, top_dir: str) -> SimpleReturnsDataFrame:
        """
        Given the path to a directory it searches for all Timeseries excel files, which are related to various
        strategies. Each standard Timeseries file contains an index in the first column and timeseries of the portfolio
        in the second column. The first row contains header ("Index" for the first column and the backtest name for the
        second column).

        After all timeseries' are found, they are merged into one dataframe, indexed by dates, where each column
        represents simple returns of a strategy (column names contain the backtest names).

        Parameters
        ----------
        top_dir: str
            path to the top directory

        Returns
        -------
        SimpleReturnsDataFrame
            dataframe containing simple returns of all strategies
        """
        data_frames = []

        p = Path(top_dir)
        for excel_tms in p.glob(r"**/*Timeseries.xlsx"):
            df = PricesDataFrame(pd.read_excel(excel_tms, index_col=0))
            data_frames.append(df)

        df = pd.concat(data_frames, axis=1).to_simple_returns()
        return df

    def _add_minimum_backtest_length_plot(self, estimated_maximum: float = 1.0):
        self.document.add_element(HeadingElement(level=2, text="Minimum Backtest Length\n"))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("The Minimum Backtest Length (MinBTL) denotes the minimum "
                                                   "length of the backtests in years, needed to avoid selecting a "
                                                   "strategy with an In-Sample Sharpe ratio of {} among N independent"
                                                   "strategies with an expected Out-Of-Sample Sharpe ratio of zero.\n"
                                                   "MinBTL should be considered a necessary, non-sufficient condition "
                                                   "to avoid overfitting.".format(estimated_maximum)))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("The concept of the Minimum Backtest Length is presented by Bailey, "
                                                   "Borwein, Prado and Zhu in  the 'PseudoMathematics and Financial "
                                                   "Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample "
                                                   "Performance'. The paper contains the exact way of computing the"
                                                   "MinBTL and the upper bound estimate of that measure (both measures "
                                                   "are showed on the plot)."))
        self.document.add_element(ParagraphElement("\n"))

        chart = LineChart(start_x=0)
        legend = LegendDecorator(key="legend_decorator")
        title_decorator = TitleDecorator("Minimum Backtest Length", key="title")
        chart.add_decorator(title_decorator)
        axes_decorator = AxesLabelDecorator(x_label='number of trials', y_label='number of years')
        chart.add_decorator(axes_decorator)

        max_number_of_backtests = 100
        number_of_trials = QFSeries(data=range(1, max_number_of_backtests))

        # Color scheme used for the chart
        red_color = "#A40000"
        green_color = "#00a452"

        # Compute both data series that need to be plotted
        def upper_bound_minBTL(samples_number: int):
            return 2 * np.log(samples_number) / (estimated_maximum ** 2)

        upper_bound_series = QFSeries(data=[upper_bound_minBTL(no) for no in number_of_trials],
                                      index=range(1, max_number_of_backtests))
        minimum_backtest_length_series = minBTL(number_of_trials, estimated_maximum)

        # Add both data series to the plot
        minimum_backtest_length = DataElementDecorator(minimum_backtest_length_series, linewidth=1.5, color=red_color)
        chart.add_decorator(minimum_backtest_length)
        legend.add_entry(minimum_backtest_length, "MinBTL")

        upper_bound = DataElementDecorator(upper_bound_series, linewidth=1.5, linestyle='dashed', color=green_color)
        chart.add_decorator(upper_bound)
        legend.add_entry(upper_bound, "Upper bound for MinBTL")

        # Check if in our case the condition is satisfied
        condition_satisfied = self.backtests_length > minBTL(self.number_of_strategies)

        # Add the point with current values, emphasize it with appropriate colour
        chart.add_decorator(legend)
        dot_color = green_color if condition_satisfied else red_color
        actual_values = ScatterDecorator([self.number_of_strategies], [round(self.backtests_length)], color=dot_color)
        chart.add_decorator(actual_values)

        text_decorator = TextDecorator("({:.0f}, {:.2f})".format(self.number_of_strategies, self.backtests_length),
                                       y=DataCoordinate(self.backtests_length - 0.3),
                                       x=DataCoordinate(self.number_of_strategies + max_number_of_backtests / 50))
        chart.add_decorator(text_decorator)

        # Fill area below blue line in red with semitransparent red color
        red_fill = FillBetweenDecorator(upper_bound=minimum_backtest_length_series, colors_alpha=0.1, color=red_color)
        chart.add_decorator(red_fill)

        # Fill the area above dotted (or blue ?) line with semitransparent green color
        y_upper_bound = max([upper_bound_series.max() + 1, self.backtests_length + 1])
        green_fill = FillBetweenDecorator(lower_bound=minimum_backtest_length_series,
                                          colors_alpha=0.1, color=green_color,
                                          upper_bound=QFSeries(
                                              data=[y_upper_bound for _ in range(1, max_number_of_backtests)],
                                              index=range(1, max_number_of_backtests)))
        chart.add_decorator(green_fill)

        self.document.add_element(ChartElement(chart, figsize=self.full_image_size, dpi=self.dpi))

        # Add final conclusions
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("As it can be seen from the plot, the Minimum Backtest Length "
                                                   "condition in the case of our backtests is {}."
                                                   "The MinBTL for {} backtests equals {:.2f} years and the number of "
                                                   "years we used for the backtests was equal to {:.2f}.".format(
                                                        "satisfied" if condition_satisfied else "not satisfied",
                                                        self.number_of_strategies, minBTL(self.number_of_strategies),
                                                        self.backtests_length)))
        self.document.add_element(ParagraphElement("\n"))

    def _add_probability_of_backtest_overiftting_analysis(self):
        """ Computes the PBO and other necessary measures to asses the probability of the backtests overfitting. """
        self.document.add_element(HeadingElement(level=2, text="Probability of Backtest Overfitting ({} slices)"
                                                 .format(self.num_of_slices)))
        self.document.add_element(ParagraphElement("\n"))

        self.document.add_element(ParagraphElement("The concept of the Probability of Backtest Overfitting is "
                                                   "presented by Bailey, Borwein, Prado and Zhu in the "
                                                   "'The Probability of Backtest Overfitting'. The paper contains the "
                                                   "exact way of computing all the below described measures."))
        self.document.add_element(ParagraphElement("\n"))

        self.document.add_element(HeadingElement(level=3, text="Probability of Backtest Overfitting (PBO)"))
        self.document.add_element(ParagraphElement("The probability that the model configuration selected as optimal "
                                                   "In-Sample will underperform the median of the N model "
                                                   "configurations Out-Of-Sample.\n"))

        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(HeadingElement(level=3, text="Probability of loss"))
        self.document.add_element(ParagraphElement("The probability that the model selected as optimal IS will deliver "
                                                   "a loss OOS. Even if PBO is close to 0 the probability of loss "
                                                   "could be high, in which case the strategy’s performance OOS is "
                                                   "probably poor for reasons other than overfitting.\n"))
        self.document.add_element(ParagraphElement("\n"))

        self.document.add_element(HeadingElement(level=3, text="Expected return"))
        self.document.add_element(ParagraphElement("For each combination of the IS-OOS sets, the best IS strategy is "
                                                   "chosen (based on the chosen Measure, e.g. sharpe ratio) and its "
                                                   "OOS annualised return is computed. Expected return "
                                                   "equals to the mean value of all OOS annualised returns of best "
                                                   "strategies.\n"))
        self.document.add_element(ParagraphElement("\n"))

        self.document.add_element(HeadingElement(level=3, text="Results"))
        pbo_values = [(fun_name,
                       "{:.2%}".format(oa.calculate_overfitting_probability()),
                       "{:.2%}".format(oa.calculate_probability_of_loss()),
                       "{:.2%}".format(oa.calculate_expected_return()))
                      for fun_name, oa in self.overfitting_analysis.items()]

        table = DFTable(QFDataFrame.from_records(pbo_values,
                                                 columns=["Measure name*",
                                                          "Probability of Backtest Overfitting",
                                                          "Probability of loss",
                                                          "Expected return"]),
                        css_classes=['table', 'left-align'])
        self.document.add_element(table)
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("(*) Measure defines a method used to select the optimal strategy "
                                                   "In-Sample."))

    def _add_best_is_and_oos_qualities(self):
        self.document.add_element(NewPageElement())
        self.document.add_element(HeadingElement(level=3, text="Performance degradation"))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("The plots show the pairs of (In-Sample performance, Out-Of-Sample "
                                                   "performance) for the optimal model configurations selected for "
                                                   "each subset, which corresponds to the performance degradation "
                                                   "associated with the backtest of the investment strategy."))

        self.document.add_element(ParagraphElement("\n"))
        for function_name, oa in self.overfitting_analysis.items():
            grid = self._get_new_grid()

            df = oa.get_best_strategies_is_oos_qualities()

            chart = LineChart()
            chart.add_decorator(ScatterDecorator(x_data=df["IS"], y_data=df["OOS"], size=10))
            chart.add_decorator(TitleDecorator("IS vs OOS comparison - {}".format(function_name), key="title"))
            chart.add_decorator(
                AxesLabelDecorator(x_label="In-Sample performance", y_label="Out-Of-Sample performance"))
            grid.add_chart(chart)

            slope, intercept = polyfit(df["IS"], df["OOS"])
            x_range = np.linspace(df["IS"].min(), df["IS"].max(), num=200)
            y_range = slope * x_range + intercept
            chart.add_decorator(DataElementDecorator(QFSeries(index=x_range, data=y_range),
                                                     color="black", linestyle="dashed", linewidth=1))

            text_position_shift = (df["OOS"].max() - df["OOS"].min()) / 20
            chart.add_decorator(TextDecorator("y = {:.2f}x + {:.2f}".format(slope, intercept),
                                              x=DataCoordinate(x_range[len(x_range) // 2]),
                                              y=DataCoordinate(y_range[len(x_range) // 2] + text_position_shift),
                                              size=8))

            grid.add_chart(self._get_is_oos_fit_chart(oa, top_strategies_to_plot=4))

            is_hist = HistogramChart(df["IS"], best_fit=True, bins=20)
            is_hist.add_decorator(TitleDecorator(title="In-Sample {} histogram".format(function_name), key="title"))
            grid.add_chart(is_hist)

            oos_hist = HistogramChart(df["OOS"], best_fit=True, bins=20)
            oos_hist.add_decorator(
                TitleDecorator(title="Out-Of-Sample {} histogram".format(function_name), key="title"))
            grid.add_chart(oos_hist)

            self.document.add_element(grid)

    def _add_logits_distribution_chart(self):
        self.document.add_element(NewPageElement())
        self.document.add_element(HeadingElement(level=3, text="Logits' distribution"))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("The charts below display the distribution of logits, which allows "
                                                   "us to compute the probability of backtest overfitting (PBO). This "
                                                   "represents the rate at which optimal In-Sample strategies "
                                                   "underperform the median of the OOS trials."))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("Logit is a value calculated for each combination, i.e. each "
                                                   "In-Sample and Out-Of-Sample pair. In order to calculate single "
                                                   "logit the best IS strategy is picked and then it is ranked OOS. "
                                                   "Single logit corresponds to the performance ranking of the "
                                                   "selected strategy OOS. The lower the logit is, the worse the "
                                                   "strategy OOS performance was in comparison to other strategies. "
                                                   "The higher the value of the logit, the better it performed OOS in "
                                                   "comparison to other strategies. If the value of the logit is equal "
                                                   "to 0, it means that the selected strategy was the median of all "
                                                   "strategies sorted by their OOS performance. The more negative "
                                                   "logits there are, the higher Probability of Backtest Overfitting. "
                                                   "The PBO is defined as a ratio of the number of the negative logits "
                                                   "to total number of logits)."))
        self.document.add_element(ParagraphElement("\n"))
        grid = self._get_new_grid()

        for function_name, oa in self.overfitting_analysis.items():
            logits = oa.calculate_relative_rank_logits(oa.best_is_strategies_names)
            logits_histogram = HistogramChart(logits.values, best_fit=True)
            logits_histogram.add_decorator(TitleDecorator(title="Logits distribution - {}".format(function_name),
                                                          key="title"))
            logits_histogram.add_decorator(AxesLabelDecorator(x_label="Logit value", y_label="Frequency"))
            grid.add_chart(logits_histogram)

        self.document.add_element(grid)

    def _add_stochastic_dominance_chart(self):
        self.document.add_element(NewPageElement())
        self.document.add_element(HeadingElement(level=3, text="Stochastic dominance"))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("This analysis determines whether the procedure used to select a "
                                                   "strategy In-Sample is preferable to randomly choosing one model "
                                                   "configuration among the N alternatives."))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("Stochastic dominance shows the advantage or disadvantage of using "
                                                   "particular algorithm selection methodology. Each line on the chart "
                                                   "is a Cumulative-Density-Function (CDF) of the performance measure. "
                                                   "CDF function tell the probability that performance will take a "
                                                   "value less than or equal to x."))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(HeadingElement(level=3, text="Optimised case"))
        self.document.add_element(ParagraphElement("For each IS-OOS combination the best IS strategy is picked. Then "
                                                   "the OOS performance of this strategy is evaluated. Optimised line "
                                                   "corresponds to the CDF of OOS performances across all combinations."))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(HeadingElement(level=3, text="Non-optimised case"))
        self.document.add_element(ParagraphElement("For each IS-OOS combination median value of the OOS is calculated "
                                                   "across all tested strategies. Non-optimised line corresponds to "
                                                   "the CDF function of these median values across all combinations."))
        self.document.add_element(ParagraphElement("\n"))
        self.document.add_element(ParagraphElement("First-order stochastic dominance occurs if CDF of OOS performance "
                                                   "measures for the best IS strategy (optimized line) is below the "
                                                   "CDF of the median OOS performance (Non-optimised). In other words, "
                                                   "the distribution of the OOS performance measures is shifted "
                                                   "towards positive values when we use the best IS strategy."))
        self.document.add_element(ParagraphElement("\n"))

        grid = self._get_new_grid()
        for function_name, oa in self.overfitting_analysis.items():
            oos_qualities = oa.get_best_strategies_is_oos_qualities()["OOS"]

            x_values = np.sort(oos_qualities.values)
            best_qualities = QFSeries(data=stats.ecdf(x_values).cdf.probabilities, index=x_values)

            all_oos_qualities = [oos_ranking["quality"].median() for oos_ranking in oa.oos_ranking]

            x_values = np.sort(all_oos_qualities)
            qualities = QFSeries(data=stats.ecdf(x_values).cdf.probabilities, index=x_values)

            # Adjust the end of the lines
            if best_qualities.idxmax() < qualities.idxmax() and max(best_qualities) == 1:
                best_qualities.loc[qualities.idxmax()] = 1.0
            elif best_qualities.idxmax() > qualities.idxmax() and max(qualities) == 1:
                qualities.loc[best_qualities.idxmax()] = 1.0

            chart = LineChart()
            legend = LegendDecorator()
            data = DataElementDecorator(best_qualities)
            chart.add_decorator(data)
            legend.add_entry(data, "Optimised")

            data = DataElementDecorator(qualities)
            chart.add_decorator(data)
            legend.add_entry(data, "Non-optimised")

            chart.add_decorator(legend)
            chart.add_decorator(TitleDecorator("Stochastic dominance - {}".format(function_name)))
            grid.add_chart(chart)

        self.document.add_element(grid)

    def _get_is_oos_fit_chart(self, oa: OverfittingAnalysis, top_strategies_to_plot: int = 4) -> Chart:
        # Find top best OOS / IS performing strategies
        mean_quality_for_each_strategy_in_oos = pd.concat(
            [oos_qualities["quality"] for oos_qualities in oa.oos_ranking], axis=1).mean(axis=1)
        top_strategies_names = mean_quality_for_each_strategy_in_oos.nlargest(top_strategies_to_plot).index

        chart = LineChart()
        legend = LegendDecorator()

        def func(x, a, b, c):
            return a * np.exp(-b * x) + c

        min_is_performance = min([i["quality"].min() for i in oa.is_ranking])
        max_is_performance = max([i["quality"].max() for i in oa.is_ranking])
        x_range = np.linspace(min_is_performance, max_is_performance, 1000)

        for ind, strategy in enumerate(top_strategies_names):
            try:
                is_vs_oos = sorted([(is_qualities.loc[strategy, "quality"], oos_qualities.loc[strategy, "quality"])
                                    for is_qualities, oos_qualities in zip(oa.is_ranking, oa.oos_ranking)],
                                   key=lambda i: i[0])

                popt, _ = curve_fit(func, [i[0] for i in is_vs_oos], [i[1] for i in is_vs_oos], maxfev=5000)
                data_points = QFSeries(index=x_range, data=[func(x, *popt) for x in x_range])
                data_element = DataElementDecorator(data_points)
                chart.add_decorator(data_element)
                legend.add_entry(data_element, "TOP {}".format(ind + 1))
            except Exception as e:
                self.logger.error(e)

        chart.add_decorator(TitleDecorator("IS vs OOS fitted line - TOP {}".format(top_strategies_to_plot)))
        chart.add_decorator(
            AxesLabelDecorator(x_label="In-Sample performance", y_label="Out-Of-Sample performance"))
        chart.add_decorator(legend)
        return chart

    def save(self, report_dir: str = ""):
        # Set the style for the report
        plt.style.use(['tearsheet'])

        filename = "%Y_%m_%d-%H%M {}.pdf".format(self.title)
        filename = datetime.now().strftime(filename)
        return self.pdf_exporter.generate([self.document], report_dir, filename)
