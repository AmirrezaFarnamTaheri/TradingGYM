from .base import assess, IntegrationReport

def assess_finrl(evidence): return assess('finrl', evidence, ('offline datasets', 'deterministic seeds', 'environment parity', 'network isolation'))
def assess_lean(evidence): return assess('lean', evidence, ('no-network container', 'broker paths absent', 'normalized fills and holdings', 'differential tolerance policy'))
def assess_qlib(evidence): return assess('qlib', evidence, ('frozen dataset parity', 'no global configuration', 'safe serialization', 'deterministic benchmarks'))
def assess_skfolio(evidence): return assess('skfolio', evidence, ('numerical golden fixtures', 'solver timeout', 'time-aware cross-validation', 'cost-aware scoring'))
def assess_zipline_reloaded(evidence): return assess('zipline-reloaded', evidence, ('calendar normalization', 'corporate-action parity', 'no live execution', 'frozen data'))
