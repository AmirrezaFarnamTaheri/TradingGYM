from .base import assess, IntegrationReport

def assess_opa(evidence): return assess('opa', evidence, ('signed bundle digest', 'fail-closed timeout', 'decision replay', 'break-glass governance'))
