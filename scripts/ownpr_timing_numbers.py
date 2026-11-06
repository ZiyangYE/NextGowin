"""Preserve negative-zero slack from rounded native timing reports."""
import math


def negative_slack(value):
    number=float(value)
    if not math.isfinite(number):raise ValueError('Nonfinite timing slack')
    return number<0 or number==0 and math.copysign(1.,number)<0
