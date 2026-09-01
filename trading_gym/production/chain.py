SUPPORTED_CHAINS={1:'ethereum',56:'bsc',137:'polygon',42161:'arbitrum'}
def normalize_chain(chain_id):
    cid=int(chain_id)
    if cid not in SUPPORTED_CHAINS: raise ValueError('unsupported chain')
    return {'chain_id':cid,'name':SUPPORTED_CHAINS[cid]}
