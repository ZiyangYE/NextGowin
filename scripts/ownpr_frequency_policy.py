"""User-authorized direct native Fmax reporting after complete 110 MHz STA."""
import math

TARGET_MULTIPLIER=1.20
STA_HEADROOM_MULTIPLIER=1.02


def target_evaluation_mhz(baseline_mhz):
    if type(baseline_mhz) not in (int,float) or not math.isfinite(baseline_mhz) or baseline_mhz<=0:
        raise ValueError('Invalid fair baseline Fmax')
    return baseline_mhz*TARGET_MULTIPLIER*STA_HEADROOM_MULTIPLIER


def target_relative_fmax_acceptance(metrics,baseline_mhz,evaluation_mhz):
    """Frequency component only; caller must verify complete source-matched gates.

    Same rule for official and own configurations. A passing decision does not
    assert the 20% target or setup closure at the higher evaluation constraint.
    """
    expected=target_evaluation_mhz(baseline_mhz)
    if type(evaluation_mhz) not in (int,float) or not math.isfinite(evaluation_mhz) or not math.isclose(evaluation_mhz,expected,rel_tol=1e-8,abs_tol=1e-8):
        raise ValueError('STA frequency differs from predeclared baseline-relative rule')
    for name in ('reported_fmax_mhz','hold_wns_ns','setup_wns_ns'):
        if type(metrics[name]) not in (int,float) or not math.isfinite(metrics[name]):raise ValueError('Invalid native metric '+name)
    for name in ('hold_violations','setup_violations'):
        if type(metrics[name]) is not int or metrics[name]<0:raise ValueError('Invalid native count '+name)
    if metrics['reported_fmax_mhz']<=0:raise ValueError('Invalid native Fmax')
    accepted=metrics['hold_violations']==0 and metrics['hold_wns_ns']>=0 and metrics['other_checks_passed'] is True
    ratio=metrics['reported_fmax_mhz']/baseline_mhz
    return dict(accepted=accepted,accepted_fmax_mhz=metrics['reported_fmax_mhz'] if accepted else None,
        frequency_acceptance_basis='native_reported_fmax_target_relative_sta_passing_hold_and_other_checks',
        sta_evaluation_mhz=evaluation_mhz,sta_headroom_multiplier=STA_HEADROOM_MULTIPLIER,
        target_mhz=baseline_mhz*TARGET_MULTIPLIER,fmax_ratio=ratio,
        frequency_target_achieved=accepted and ratio>=TARGET_MULTIPLIER,
        setup_wns_ns=metrics['setup_wns_ns'],hold_wns_ns=metrics['hold_wns_ns'],
        full_timing_pass_at_evaluation=bool(metrics['native_constraints_satisfied'] and metrics['setup_violations']==0 and metrics['setup_wns_ns']>=0 and accepted),frequency_replayed=False)


def reported_fmax_acceptance(metrics,evaluation_mhz=110.):
    """Frequency decision only; caller must audit logic, I/O and configuration."""
    if not math.isfinite(evaluation_mhz) or abs(evaluation_mhz-110.)>1e-8:raise ValueError('Direct Fmax policy requires 110 MHz evaluation')
    for name in ('reported_fmax_mhz','hold_wns_ns'):
        if type(metrics[name]) not in (int,float) or not math.isfinite(metrics[name]):raise ValueError('Invalid native frequency/hold metric')
    if metrics['reported_fmax_mhz']<=0:raise ValueError('Invalid native Fmax')
    accepted=metrics['hold_violations']==0 and metrics['hold_wns_ns']>=0 and metrics['other_checks_passed']
    return dict(accepted=bool(accepted),accepted_fmax_mhz=metrics['reported_fmax_mhz'] if accepted else None,
                frequency_acceptance_basis='native_reported_fmax_from_complete_110mhz_sta_with_passing_hold',
                sta_evaluation_mhz=110.,frequency_replayed=False,
                full_timing_pass_at_110mhz=bool(metrics['native_constraints_satisfied']))
