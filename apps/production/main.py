from __future__ import annotations
import json
from pathlib import Path
try:
    import typer
    app=typer.Typer(add_completion=False)
except Exception:
    typer=None; app=None
from trading_gym.risk.analytics import tail_risk
from trading_gym.production.status import build_status
from trading_gym.institutional_os.finance_skill_chain_assurance import evaluate_finance_skill_chain
from trading_gym.institutional_os.financial_ui_projection_assurance import evaluate_financial_ui_projection
from trading_gym.institutional_os.equity_research_assembly_assurance import evaluate_equity_research_assembly
from trading_gym.institutional_os.peer_wave18 import search_wave18_peer_catalog,evaluate_wave18_catalog_saturation

def _load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def _emit(v):
    if hasattr(v,'model_dump'): v=v.model_dump()
    text=json.dumps(v,sort_keys=True,default=str); print(text); return v

def finance_skill_chain_assess(path): return _emit(evaluate_finance_skill_chain(_load(path)))
def financial_ui_projection_assess(path): return _emit(evaluate_financial_ui_projection(_load(path)))
def equity_research_assembly_assess(path): return _emit(evaluate_equity_research_assembly(_load(path)))
def peer_wave18_search(query,limit=50): return _emit(search_wave18_peer_catalog(query,limit=limit))
def peer_wave18_saturation(): return _emit(evaluate_wave18_catalog_saturation())
if app is not None:
    app.command('finance-skill-chain-assess')(finance_skill_chain_assess)
    app.command('financial-ui-projection-assess')(financial_ui_projection_assess)
    app.command('equity-research-assembly-assess')(equity_research_assembly_assess)
    app.command('peer-wave18-search')(peer_wave18_search)
    app.command('peer-wave18-saturation')(peer_wave18_saturation)


def finance_view(path):
    from trading_gym.visualization.terminal_blocks import render_finance_bundle
    return render_finance_bundle(_load(path))


def peer_wave14_search(query, limit=50):
    from trading_gym.institutional_os.peer_wave14 import search_wave14_peer_catalog
    return _emit(search_wave14_peer_catalog(query, limit=limit))


def peer_wave14_saturation():
    from trading_gym.institutional_os.peer_wave14 import evaluate_wave14_catalog_saturation
    return _emit(evaluate_wave14_catalog_saturation())

if app is not None:
    app.command('finance-view')(finance_view)
    app.command('peer-wave14-search')(peer_wave14_search)
    app.command('peer-wave14-saturation')(peer_wave14_saturation)
