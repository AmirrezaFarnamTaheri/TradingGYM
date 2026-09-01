from .base import assess, IntegrationReport

def assess_temporal(evidence): return assess('temporal', evidence, ('deterministic replay', 'worker versioning', 'persistence restore', 'backpressure', 'history retention'))
