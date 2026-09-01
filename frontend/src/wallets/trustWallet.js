export async function connectTrustWallet() {
  const provider = window.ethereum;
  if (!provider) throw new Error('No injected wallet provider found');
  const accounts = await provider.request({ method: 'eth_requestAccounts' });
  const chainIdHex = await provider.request({ method: 'eth_chainId' });
  return { address: accounts[0], chainId: Number.parseInt(chainIdHex, 16), provider: 'trust-wallet-compatible', custody: false };
}
export function assertNoSecretPayload(payload) {
  for (const key of Object.keys(payload || {})) if (/private.?key|mnemonic|seed/i.test(key)) throw new Error('Secret material is forbidden');
  return true;
}
