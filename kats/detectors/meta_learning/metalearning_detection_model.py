# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict

import ast
import logging
import math
from typing import Any, Dict, List, Optional, Set, Union

import pandas as pd

from kats.consts import TimeSeriesData
from kats.models.metalearner.metalearner_modelselect import MetaLearnModelSelect
from kats.tsfeatures.tsfeatures import TsFeatures


NUM_SECS_IN_DAY: int = 3600 * 24
PARAMS_TO_SCALE_DOWN = {"historical_window", "scan_window"}
MIN_EXAMPLES = 30


def get_ts_features(ts: TimeSeriesData) -> Dict[str, float]:
    """
    Extract TSFeatures for the input time series.
    """
    # Run Kats TsFeatures
    ts_features = TsFeatures(hw_params=False)
    feats = ts_features.transform(ts)

    # Rounding features
    features = {}
    assert isinstance(feats, dict)
    for feature_name, feature_val in feats.items():
        if not math.isnan(feature_val):
            feature_val = format(feature_val, ".4f")
        features[feature_name] = feature_val

    return features


def change_dtype(d: Dict[str, Any]) -> Dict[str, float]:
    for elm in d:
        d[elm] = float(d[elm])
    return d


def change_str_to_dict(x: Union[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if isinstance(x, dict):
        return x
    elif isinstance(x, str):
        try:
            dict_str = ast.literal_eval(x)
            return dict_str
        except Exception as e:
            print(e)
    else:
        raise ValueError(f"type is {type(x)}")


def metadata_detect_preprocessor(
    rawdata: pd.DataFrame,
    params_to_scale_down: Set[str] = PARAMS_TO_SCALE_DOWN,
) -> List[Dict[str, Any]]:
    rawdata["features"] = rawdata["features"].map(change_str_to_dict)
    rawdata["features"] = rawdata["features"].map(change_dtype)
    rawdata["hpt_res"] = rawdata["hpt_res"].map(change_str_to_dict)

    table = [
        {
            "hpt_res": rawdata["hpt_res"].iloc[i],
            "features": rawdata["features"].iloc[i],
            "best_model": rawdata["best_model"].iloc[i],
        }
        for i in range(len(rawdata))
    ]

    for ts_data in table:
        for hpt_vals in ts_data["hpt_res"].values():
            params = hpt_vals[0]
            for param in params.keys():
                if param in params_to_scale_down:
                    params[param] = params[param] / NUM_SECS_IN_DAY

    return table


class MetaDetectModelSelect:
    """
    Meta-learner framework on detection model selection.
    This framework uses classification algorithms to recommend suitable detection models.
    For training, it uses time series features as inputs and the best detection models as labels.
    For prediction, it takes time series or time series features as inputs to predict the most suitable detection model.

    Attributes:
        metadata: pd.DataFrame;
            A list of dictionaries representing the meta-data of time series
            (e.g., the meta-data generated by Get_MetaLearn_Detect_Inputs object).
            Each dictionary d must contain at least 3 components: 'hpt_res', 'features' and 'best_model'.
            d['hpt_res'] represents the best hyper-parameters for each candidate model and the corresponding errors;
            d['features'] are time series features, and d['best_model'] is a string representing the best candidate model
            of the corresponding time series data.

    Sample Usage:
        >>> mdms = MetaDetectModelSelect(data)
        >>> mdms.train(n_trees=200, test_size=0.1, eval_method='mean') # Train a meta-learner model selection model.
        >>> mdms.report_metrics() # present training results
        >>> mdms.predict(TSdata) # Predict the most suitable detection model.
        >>> mdms.fit_results() # present fitting results
        >>> mdms.pred_by_feature(eval_df) # Predict the most suitable detection model by features.
    """

    def __init__(
        self,
        metadata_df: pd.DataFrame,
        params_to_scale_down: Set[str] = PARAMS_TO_SCALE_DOWN,
    ) -> None:
        if not isinstance(metadata_df, pd.DataFrame):
            msg = "Dataset is not in form of a dataframe!"
            logging.error(msg)
            raise ValueError(msg)

        if len(metadata_df) <= MIN_EXAMPLES:
            msg = "Dataset is too small to train a meta learner!"
            logging.error(msg)
            raise ValueError(msg)

        expected_cols = ["hpt_res", "features", "best_model"]
        for col in expected_cols:
            if col not in metadata_df:
                msg = f"Missing column {col}, not able to train a meta learner!"
                logging.error(msg)
                raise ValueError(msg)

        self.metadata_df: pd.DataFrame = metadata_df
        self.results: Dict[str, Any] = {}
        self.mlms: Optional[MetaLearnModelSelect] = None
        self.params_to_scale_down: Set[str] = params_to_scale_down

    def _preprocess(self) -> List[Dict[str, Any]]:
        # prepare the training data
        # Create training data table
        return metadata_detect_preprocessor(self.metadata_df, self.params_to_scale_down)

    def train(
        self,
        method: str = "RandomForest",
        eval_method: str = "mean",
        test_size: float = 0.1,
        n_trees: int = 500,
        n_neighbors: int = 5,
    ) -> Dict[str, Any]:
        """
        Call the train() method of MetaLearnModelSelect, which returns
        {
            "fit_error": fit_error,
            "pred_error": pred_error,
            "clf_accuracy": metrics.accuracy_score(y_test, y_pred),
        }
        """
        self.mlms = MetaLearnModelSelect(self._preprocess())
        self.results = self.mlms.train(
            method=method,
            eval_method=eval_method,
            test_size=test_size,
            n_trees=n_trees,
            n_neighbors=n_neighbors,
        )
        return self.results

    def report_metrics(self) -> pd.DataFrame:
        # report the summary, as in the notebook N1154788
        if self.results is None:
            self.results = self.train()

        summary = pd.DataFrame(
            [self.results["fit_error"], self.results["pred_error"]], copy=False
        )
        summary["type"] = ["fit_error", "pred_error"]
        summary["error_metric"] = "Inverted F-score"
        return summary

    def predict(self, ts: TimeSeriesData) -> str:
        # extract features from the ts
        feature_list = [get_ts_features(ts)]
        feature_list = [change_dtype(x) for x in feature_list]
        feature_df = pd.DataFrame(feature_list)

        if self.mlms is None:
            msg = "Please train a classifier first."
            logging.error(msg)
            raise ValueError(msg)
        algo_list = self.mlms.pred_by_feature(feature_df.values)
        return algo_list[0]

    def fit_results(self) -> pd.DataFrame:
        """
        Present fitting results
        """
        # extract features from the dataframe
        feature_list = [
            change_str_to_dict(self.metadata_df["features"].iloc[i])
            for i in range(len(self.metadata_df.features))
        ]

        # pyre-fixme[6]
        feature_list = [change_dtype(x) for x in feature_list]
        feature_df = pd.DataFrame(feature_list)

        if self.mlms is None:
            msg = "Please train a classifier first."
            logging.error(msg)
            raise ValueError(msg)
        algo_list = self.mlms.pred_by_feature(feature_df.values)

        label_df = pd.DataFrame({"best_model": algo_list})

        return label_df

    def pred_by_feature(self, eval_df: pd.DataFrame) -> pd.DataFrame:
        """
        Extract features from the dataframe
        eval_df has one columns: features -> dict
        """
        feature_list = [
            change_str_to_dict(eval_df["features"].iloc[i])
            for i in range(len(eval_df.features))
        ]

        # pyre-fixme[6]
        feature_list = [change_dtype(x) for x in feature_list]
        feature_df = pd.DataFrame(feature_list)

        assert self.mlms is not None
        algo_list = self.mlms.pred_by_feature(feature_df.values)

        label_df = pd.DataFrame({"best_model": algo_list})

        return label_df
