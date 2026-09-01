const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const sourcePath = path.join(__dirname, 'trustWallet.js');
  const source = fs.readFileSync(sourcePath, 'utf8');
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  const wallet = await import(moduleUrl);
  assert.equal(wallet.assertNoSecretPayload({ address: '0xabc' }), true);
  assert.throws(() => wallet.assertNoSecretPayload({ private_key: 'secret' }), /forbidden/i);
  global.window = {
    ethereum: {
      request: async ({ method }) => method === 'eth_requestAccounts'
        ? ['0x' + 'a'.repeat(40)]
        : '0x1',
    },
  };
  const connection = await wallet.connectTrustWallet();
  assert.equal(connection.chainId, 1);
  assert.equal(connection.custody, false);
  assert.equal('privateKey' in connection, false);
  delete global.window;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
