from dataclasses import dataclass
@dataclass(frozen=True)
class WalletConnection: chain_id:int; address:str; provider:str='trust-wallet'; connected:bool=True
class NonCustodialWalletRegistry:
    def admit(self, *, chain_id, address, provider='trust-wallet'):
        if not address.startswith('0x') or len(address)!=42: raise ValueError('invalid address')
        return WalletConnection(int(chain_id),address,provider,True)
