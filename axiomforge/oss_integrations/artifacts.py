from .base import assess, IntegrationReport

def assess_lakefs(evidence): return assess('lakefs', evidence, ('metadata backup', 'object restore', 'commit immutability', 'retention and garbage collection'))
