from .base import assess, IntegrationReport

def assess_openlineage(evidence): return assess('openlineage', evidence, ('golden event fixtures', 'custom facet governance', 'delivery retry and deduplication'))
