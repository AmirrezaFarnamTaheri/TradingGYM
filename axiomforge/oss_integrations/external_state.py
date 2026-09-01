from .base import assess, IntegrationReport

def assess_postgresql(evidence): return assess('postgresql', evidence, ('tenant isolation', 'CAS and fencing', 'migration', 'backup and restore', 'failover drill'))
