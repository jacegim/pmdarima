# -*- coding: utf-8 -*-

from __future__ import absolute_import

from pyramid.arima import ARIMA, auto_arima
from pyramid.arima.arima import VALID_SCORING
from pyramid.arima.auto import _fmt_warning_str
from pyramid.arima.utils import nsdiffs
from pyramid.datasets import load_lynx, load_wineind, load_heartrate
from pyramid.utils import get_callable, assert_raises

import numpy as np
from numpy.testing import assert_array_almost_equal, assert_almost_equal
from numpy.random import RandomState

from statsmodels import api as sm
import pandas as pd

import warnings
import pickle
import os

# initialize the random state
rs = RandomState(42)
y = rs.rand(25)

# more interesting, heart rate data:
hr = load_heartrate()

# > set.seed(123)
# > abc <- rnorm(50, 5, 1)
abc = np.array([4.439524, 4.769823, 6.558708, 5.070508,
                5.129288, 6.715065, 5.460916, 3.734939,
                4.313147, 4.554338, 6.224082, 5.359814,
                5.400771, 5.110683, 4.444159, 6.786913,
                5.497850, 3.033383, 5.701356, 4.527209,
                3.932176, 4.782025, 3.973996, 4.271109,
                4.374961, 3.313307, 5.837787, 5.153373,
                3.861863, 6.253815, 5.426464, 4.704929,
                5.895126, 5.878133, 5.821581, 5.688640,
                5.553918, 4.938088, 4.694037, 4.619529,
                4.305293, 4.792083, 3.734604, 7.168956,
                6.207962, 3.876891, 4.597115, 4.533345,
                5.779965, 4.916631])

wineind = load_wineind()
lynx = load_lynx()


def test_basic_arima():
    arima = ARIMA(order=(0, 0, 0), trend='c', suppress_warnings=True)
    preds = arima.fit_predict(y)  # fit/predict for coverage

    # test some of the attrs
    assert_almost_equal(arima.aic(), 11.201308403566909, decimal=5)
    assert_almost_equal(arima.aicc(), 11.74676, decimal=5)
    assert_almost_equal(arima.bic(), 13.639060053303311, decimal=5)

    # get predictions
    expected_preds = np.array([0.44079876, 0.44079876, 0.44079876,
                               0.44079876, 0.44079876, 0.44079876,
                               0.44079876, 0.44079876, 0.44079876,
                               0.44079876])

    # generate predictions
    assert_array_almost_equal(preds, expected_preds)

    # Make sure we can get confidence intervals
    expected_intervals = np.array([
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139],
        [-0.10692387,  0.98852139]
    ])

    _, intervals = arima.predict(n_periods=10, return_conf_int=True,
                                 alpha=0.05)
    assert_array_almost_equal(intervals, expected_intervals)


def test_with_oob():
    # show we can fit with CV (kinda)
    arima = ARIMA(order=(2, 1, 2), suppress_warnings=True,
                  out_of_sample_size=10).fit(y=hr)
    assert not np.isnan(arima.oob())  # show this works

    # show we can fit if ooss < 0 and oob will be nan
    arima = ARIMA(order=(2, 1, 2), suppress_warnings=True,
                  out_of_sample_size=-1).fit(y=hr)
    assert np.isnan(arima.oob())

    # This will raise since n_steps is not an int
    assert_raises(TypeError, arima.predict, n_periods="5")

    # But that we CAN forecast with an int...
    _ = arima.predict(n_periods=5)

    # Show we fail if cv > n_samples
    assert_raises(ValueError,
                  ARIMA(order=(2, 1, 2), out_of_sample_size=1000).fit, hr)


# Test Issue #28 ----------------------------------------------------------
def test_oob_for_issue_28():
    # Continuation of above: can we do one with an exogenous array, too?
    xreg = rs.rand(hr.shape[0], 4)
    arima = ARIMA(order=(2, 1, 2), suppress_warnings=True,
                  out_of_sample_size=10).fit(
        y=hr, exogenous=xreg)

    oob = arima.oob()
    assert not np.isnan(oob)

    # Assert that the endog shapes match. First is equal to the original,
    # and the second is the differenced array, with original shape - d.
    assert np.allclose(arima.arima_res_.data.endog, hr, rtol=1e-2)
    assert arima.arima_res_.model.endog.shape[0] == hr.shape[0] - 1

    # Now assert the same for exog
    assert np.allclose(arima.arima_res_.data.exog, xreg, rtol=1e-2)
    assert arima.arima_res_.model.exog.shape[0] == xreg.shape[0] - 1

    # Compare the OOB score to an equivalent fit on data - 10 obs, but
    # without any OOB scoring, and we'll show that the OOB scoring in the
    # first IS in fact only applied to the first (train - n_out_of_bag)
    # samples
    arima_no_oob = ARIMA(
            order=(2, 1, 2), suppress_warnings=True,
            out_of_sample_size=0)\
        .fit(y=hr[:-10], exogenous=xreg[:-10, :])

    scoring = get_callable(arima_no_oob.scoring, VALID_SCORING)
    preds = arima_no_oob.predict(n_periods=10, exogenous=xreg[-10:, :])
    assert np.allclose(oob, scoring(hr[-10:], preds), rtol=1e-2)

    # Show that the model parameters are exactly the same
    xreg_test = rs.rand(5, 4)
    assert np.allclose(arima.params(), arima_no_oob.params(), rtol=1e-2)

    # Now assert on the forecast differences.
    with_oob_forecasts = arima.predict(n_periods=5, exogenous=xreg_test)
    no_oob_forecasts = arima_no_oob.predict(n_periods=5,
                                            exogenous=xreg_test)

    assert_raises(AssertionError, assert_array_almost_equal,
                  with_oob_forecasts, no_oob_forecasts)

    # But after we update the no_oob model with the latest data, we should
    # be producing the same exact forecasts

    # First, show we'll fail if we try to add observations with no exogenous
    assert_raises(ValueError, arima_no_oob.add_new_observations,
                  hr[-10:], None)

    # Also show we'll fail if we try to add mis-matched shapes of data
    assert_raises(ValueError, arima_no_oob.add_new_observations,
                  hr[-10:], xreg_test)

    # Show we fail if we try to add observations with a different dim exog
    assert_raises(ValueError, arima_no_oob.add_new_observations,
                  hr[-10:], xreg_test[:, 2])

    # Actually add them now, and compare the forecasts (should be the same)
    arima_no_oob.add_new_observations(hr[-10:], xreg[-10:, :])
    assert np.allclose(with_oob_forecasts,
                       arima_no_oob.predict(n_periods=5,
                                            exogenous=xreg_test),
                       rtol=1e-2)


# Test the OOB functionality for SARIMAX (Issue #28) --------------------------
def test_oob_sarimax():
    xreg = rs.rand(wineind.shape[0], 2)
    fit = ARIMA(order=(1, 1, 1),
                seasonal_order=(0, 1, 1, 12),
                out_of_sample_size=15).fit(y=wineind, exogenous=xreg)

    fit_no_oob = ARIMA(
            order=(1, 1, 1), seasonal_order=(0, 1, 1, 12),
            out_of_sample_size=0, suppress_warnings=True)\
        .fit(y=wineind[:-15], exogenous=xreg[:-15, :])

    # now assert some of the same things here that we did in the former test
    oob = fit.oob()

    # compare scores:
    scoring = get_callable(fit_no_oob.scoring, VALID_SCORING)
    no_oob_preds = fit_no_oob.predict(n_periods=15, exogenous=xreg[-15:, :])
    assert np.allclose(oob, scoring(wineind[-15:], no_oob_preds), rtol=1e-2)

    # show params are still the same
    assert np.allclose(fit.params(), fit_no_oob.params(), rtol=1e-2)

    # show we can add the new samples and get the exact same forecasts
    xreg_test = rs.rand(5, 2)
    fit_no_oob.add_new_observations(wineind[-15:], xreg[-15:, :])
    assert np.allclose(fit.predict(5, xreg_test),
                       fit_no_oob.predict(5, xreg_test),
                       rtol=1e-2)

    # Show we can get a confidence interval out here
    preds, conf = fit.predict(5, xreg_test, return_conf_int=True)
    assert all(isinstance(a, np.ndarray) for a in (preds, conf))


# Test Issue #29 (d=0, cv=True) -----------------------------------------------
def test_oob_for_issue_29():
    dta = sm.datasets.sunspots.load_pandas().data
    dta.index = pd.Index(sm.tsa.datetools.dates_from_range('1700', '2008'))
    del dta["YEAR"]

    xreg = np.random.RandomState(1).rand(dta.shape[0], 3)

    # Try for cv on/off, various D levels, and various Xregs
    for d in (0, 1):
        for cv in (0, 3):
            for exog in (xreg, None):

                # surround with try/except so we can log the failing combo
                try:
                    model = ARIMA(order=(2, d, 0), out_of_sample_size=cv)\
                            .fit(dta, exogenous=exog)

                    # If exogenous is defined, we need to pass n_periods of
                    # exogenous rows to the predict function. Otherwise we'll
                    # just leave it at None
                    if exog is not None:
                        xr = exog[:3, :]
                    else:
                        xr = None

                    _, _ = model.predict(n_periods=3, return_conf_int=True,
                                         exogenous=xr)

                except Exception as ex:
                    print("Failing combo: d=%i, cv=%i, exog=%r"
                          % (d, cv, exog))

                    # Statsmodels can be fragile with ARMA coefficient
                    # computation. If we encounter that, pass:
                    #   ValueError: The computed initial MA coefficients are
                    #       not invertible. You should induce invertibility,
                    #       choose a different model order, or ...
                    if "invertibility" in str(ex):
                        pass
                    else:
                        raise


def _try_get_attrs(arima):
    # show we can get all these attrs without getting an error
    attrs = {
        'aic', 'aicc', 'arparams', 'arroots', 'bic', 'bse', 'conf_int',
        'df_model', 'df_resid', 'hqic', 'maparams', 'maroots',
        'params', 'pvalues', 'resid',
    }

    # this just shows all of these attrs work.
    for attr in attrs:
        getattr(arima, attr)()


def test_more_elaborate():
    # show we can fit this with a non-zero order
    arima = ARIMA(order=(2, 1, 2), suppress_warnings=True).fit(y=hr)
    _try_get_attrs(arima)

    # can we fit this same arima with a made-up exogenous array?
    xreg = rs.rand(hr.shape[0], 4)
    arima = ARIMA(order=(2, 1, 2), suppress_warnings=True).fit(
        y=hr, exogenous=xreg)
    _try_get_attrs(arima)

    # pickle this for the __get/setattr__ coverage.
    # since the only time this is tested is in parallel in auto.py,
    # this doesn't actually get any coverage proof...
    fl = 'some_temp_file.pkl'
    with open(fl, 'wb') as p:
        pickle.dump(arima, p)

    # show we can predict with this even though it's been pickled
    new_xreg = rs.rand(5, 4)
    _preds = arima.predict(n_periods=5, exogenous=new_xreg)

    # now unpickle
    with open(fl, 'rb') as p:
        other = pickle.load(p)

    # show we can still predict, compare
    _other_preds = other.predict(n_periods=5, exogenous=new_xreg)
    assert_array_almost_equal(_preds, _other_preds)

    # now clear the cache and remove the pickle file
    arima._clear_cached_state()
    os.unlink(fl)

    # now show that since we fit the ARIMA with an exogenous array,
    # we need to provide one for predictions otherwise it breaks.
    assert_raises(ValueError, arima.predict, n_periods=5, exogenous=None)

    # show that if we DO provide an exogenous and it's the wrong dims, we
    # also break things down.
    assert_raises(ValueError, arima.predict, n_periods=5,
                  exogenous=rs.rand(4, 4))


def test_the_r_src():
    # this is the test the R code provides
    fit = ARIMA(order=(2, 0, 1), trend='c', suppress_warnings=True).fit(abc)

    # the R code's AIC = ~135
    assert abs(135 - fit.aic()) < 1.0

    # the R code's AICc = ~ 137
    assert abs(137 - fit.aicc()) < 1.0

    # the R code's BIC = ~145
    assert abs(145 - fit.bic()) < 1.0

    # R's coefficients:
    #     ar1      ar2     ma1    mean
    # -0.6515  -0.2449  0.8012  5.0370

    # note that statsmodels' mean is on the front, not the end.
    params = fit.params()
    assert_almost_equal(params, np.array([5.0370, -0.6515, -0.2449, 0.8012]),
                        decimal=2)

    # > fit = forecast::auto.arima(abc, max.p=5, max.d=5,
    #             max.q=5, max.order=100, stepwise=F)
    fit = auto_arima(abc, max_p=5, max_d=5, max_q=5, max_order=100,
                     seasonal=False, trend='c', suppress_warnings=True,
                     error_action='ignore')

    # this differs from the R fit with a slightly higher AIC...
    assert abs(137 - fit.aic()) < 1.0  # R's is 135.28


def test_errors():
    def _assert_val_error(f, *args, **kwargs):
        # Legacy, didn't really assert anything. Bad news!
        # try:
        #     f(*args, **kwargs)
        #     return False
        # except ValueError:
        #     return True
        assert_raises(ValueError, f, *args, **kwargs)

    # show we fail for bad start/max p, q values:
    _assert_val_error(auto_arima, abc, start_p=-1)
    _assert_val_error(auto_arima, abc, start_q=-1)
    _assert_val_error(auto_arima, abc, max_p=-1)
    _assert_val_error(auto_arima, abc, max_q=-1)
    # (where start < max)
    _assert_val_error(auto_arima, abc, start_p=1, max_p=0)
    _assert_val_error(auto_arima, abc, start_q=1, max_q=0)

    # show max order error
    _assert_val_error(auto_arima, abc, max_order=-1)

    # show errors for d
    _assert_val_error(auto_arima, abc, max_d=-1)
    _assert_val_error(auto_arima, abc, d=-1)
    _assert_val_error(auto_arima, abc, d=5, max_d=4)

    # show error for bad IC
    _assert_val_error(auto_arima, abc, information_criterion='bad-value')

    # show bad m value
    _assert_val_error(auto_arima, abc, m=0)

    # show that for starting values > max_order, we'll get an error
    _assert_val_error(auto_arima, abc, start_p=5, start_q=5,
                      seasonal=False, max_order=3)
    _assert_val_error(auto_arima, abc, start_p=5, start_q=5, start_P=4,
                      start_Q=3, seasonal=True, max_order=3)


def test_many_orders():
    lam = 0.5
    lynx_bc = ((lynx ** lam) - 1) / lam
    auto_arima(lynx_bc, start_p=1, start_q=1, d=0, max_p=5, max_q=5,
               suppress_warnings=True, stepwise=True)


def test_small_samples():
    # if n_samples < 10, test the new starting p, d, Q
    samp = lynx[:8]
    auto_arima(samp, suppress_warnings=True, stepwise=True,
               error_action='ignore')


def test_with_seasonality1():
    fit = ARIMA(order=(1, 1, 1),
                seasonal_order=(0, 1, 1, 12),
                suppress_warnings=True).fit(y=wineind)
    _try_get_attrs(fit)

    # R code AIC result is ~3004
    assert abs(fit.aic() - 3004) < 100  # show equal within 100 or so

    # R code AICc result is ~3005
    assert abs(fit.aicc() - 3005) < 100  # show equal within 100 or so

    # R code BIC result is ~3017
    assert abs(fit.bic() - 3017) < 100  # show equal within 100 or so

    # show we can predict in-sample
    fit.predict_in_sample()

    # test with SARIMAX confidence intervals
    fit.predict(n_periods=10, return_conf_int=True, alpha=0.05)


def test_with_seasonality2():
    # also test the warning, while we're at it...
    def suppress_warnings(func):
        def suppressor(*args, **kwargs):
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("ignore")
                return func(*args, **kwargs)
        return suppressor

    @suppress_warnings
    def do_fit():
        return auto_arima(wineind, start_p=1, start_q=1, max_p=2,
                          max_q=2, m=12, start_P=0, seasonal=True, n_jobs=-1,
                          d=1, D=1, stepwise=True,
                          suppress_warnings=True,
                          error_action='ignore',
                          random_state=42)

    # show that we can forecast even after the
    # pickling (this was fit in parallel)
    seasonal_fit = do_fit()
    seasonal_fit.predict(n_periods=10)

    # ensure summary still works
    seasonal_fit.summary()


def test_with_seasonality3():
    # show we can estimate D even when it's not there...
    auto_arima(wineind, start_p=1, start_q=1, max_p=2, max_q=2, m=12,
               start_P=0, seasonal=True, d=1, D=None,
               error_action='ignore', suppress_warnings=True,
               trace=True,  # get the coverage on trace
               random_state=42, stepwise=True)


def test_with_seasonality4():
    # show we can run a random search much faster! and while we're at it,
    # make the function return all the values.
    auto_arima(wineind, start_p=1, start_q=1, max_p=2, max_q=2, m=12,
               start_P=0, seasonal=True, n_jobs=1, d=1, D=None, stepwise=False,
               error_action='ignore', suppress_warnings=True,
               random=True, random_state=42, return_valid_fits=True,
               n_fits=5)  # only fit 5


def test_with_seasonality5():
    # can we fit the same thing with an exogenous array of predictors?
    # also make it stationary and make sure that works...
    all_res = auto_arima(wineind, start_p=1, start_q=1, max_p=2,
                         max_q=2, m=12, start_P=0, seasonal=True, n_jobs=1,
                         d=1, D=None, error_action='ignore',
                         suppress_warnings=True, stationary=True,
                         random=True, random_state=42, return_valid_fits=True,
                         stepwise=False, n_fits=5,
                         exogenous=rs.rand(wineind.shape[0], 4))  # only fit 2

    # show it is a list
    assert hasattr(all_res, '__iter__')


def test_with_seasonality6():
    # show that we can fit an ARIMA where the max_p|q == start_p|q
    auto_arima(hr, start_p=0, max_p=0, d=0, start_q=0, max_q=0,
               seasonal=False, max_order=np.inf,
               suppress_warnings=True)

    # FIXME: we get an IndexError from statsmodels summary if (0, 0, 0)


def test_with_seasonality7():
    # show we can fit one with OOB as the criterion
    auto_arima(wineind, start_p=1, start_q=1, max_p=2, max_q=2, m=12,
               start_P=0, seasonal=True, n_jobs=1, d=1, D=1,
               out_of_sample_size=10, information_criterion='oob',
               suppress_warnings=True,
               error_action='raise',  # do raise so it fails fast
               random=True, random_state=42, n_fits=3,
               stepwise=False)


def test_corner_cases():
    assert_raises(ValueError, auto_arima, wineind,
                  error_action='some-bad-string')

    # things that produce warnings
    with warnings.catch_warnings(record=False):
        warnings.simplefilter('ignore')

        # show a constant result will result in a quick fit
        auto_arima(np.ones(10), suppress_warnings=True)

        # show the same thing with return_all results in the ARIMA in a list
        fits = auto_arima(np.ones(10), suppress_warnings=True,
                          return_valid_fits=True)
        assert hasattr(fits, '__iter__')

    # show we fail for n_iter < 0
    assert_raises(ValueError, auto_arima, np.ones(10), random=True, n_fits=-1)

    # show if max* < start* it breaks:
    assert_raises(ValueError, auto_arima, np.ones(10), start_p=5, max_p=0)


def test_warning_str_fmt():
    order = (1, 1, 1)
    seasonal = (1, 1, 1, 1)
    for ssnl in (seasonal, None):
        _fmt_warning_str(order, ssnl)


def test_nsdiffs_on_wine():
    assert nsdiffs(wineind, m=52) == 2