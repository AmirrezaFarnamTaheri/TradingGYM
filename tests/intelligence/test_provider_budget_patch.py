import json
import httpx
import numpy as np
import pytest
from trading_gym.intelligence.bounded_http import bounded_post, strict_response_json
from trading_gym.intelligence.deadlines import decision_budget, DecisionDeadlineExceeded
from trading_gym.intelligence.llm_key_pool import (CredentialPool, CredentialsUnavailable,
    ProviderAuthenticationError, ProviderHardQuotaError, request_with_rotation)
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import (ActionKind, BackendHealth, BackendUnavailable,
    DecisionSignal, InvalidDecisionSignal)

class Clock:
    def __init__(self):self.now=0.
    def __call__(self):return self.now

class Delayed(IntelligenceBackend):
    name="delayed"
    def __init__(self,clock,delay,error=False):self.clock,self.delay,self.error,self.calls=clock,delay,error,0
    def is_available(self):return True
    def health(self):return BackendHealth(self.name,True)
    def decide(self,context):
        self.calls+=1;self.clock.now+=self.delay
        if self.error:raise BackendUnavailable("down")
        return DecisionSignal("actual-source",ActionKind.LONG,1,.8,"test")

def test_whole_chain_budget_rejects_late_result_and_next_attempt():
    clock=Clock();a=Delayed(clock,3,True);b=Delayed(clock,3);c=Delayed(clock,0)
    p=IntelligencePolicyAdapter(a,action_low=-2,action_high=2,fallback_backends=(b,c),
        decision_timeout_seconds=5,monotonic=clock)
    with pytest.raises(DecisionDeadlineExceeded):p.predict(np.zeros(3))
    assert (a.calls,b.calls,c.calls)==(1,1,0) and p.last_signal is None

class Chunks(httpx.SyncByteStream):
    def __init__(self,chunks):self.chunks,self.closed,self.yielded=chunks,False,0
    def __iter__(self):
        for chunk in self.chunks:self.yielded+=1;yield chunk
    def close(self):self.closed=True

def test_raw_stream_closes_at_byte_limit_before_reading_rest():
    body=Chunks([b'a'*8,b'b'*8,b'never-read'])
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=body))) as client:
        with pytest.raises(InvalidDecisionSignal,match="byte_budget"):
            bounded_post(client,"https://example.invalid",headers={},json={},timeout=2,max_bytes=10)
    assert body.closed and body.yielded==2

def test_compression_and_ambiguous_json_are_rejected():
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,headers={"content-encoding":"gzip"},stream=Chunks([b'x'])))) as client:
        with pytest.raises(InvalidDecisionSignal,match="compressed"):
            bounded_post(client,"https://example.invalid",headers={},json={},timeout=2)
    for text in ('{"a":1,"a":2}','{"a":NaN}','[bad]'):
        with pytest.raises(InvalidDecisionSignal):strict_response_json(httpx.Response(200,content=text))

def test_mixed_auth_and_key_rate_limit_does_not_lock_fleet(tmp_path):
    clock=Clock();pool=CredentialPool(("KEY_A","KEY_B"),state_path=tmp_path/"pool.json",
        env={"KEY_A":"a","KEY_B":"b"},now=clock)
    responses=iter([httpx.Response(401),httpx.Response(429,headers={"retry-after":"10","x-ratelimit-scope":"key"})])
    with pytest.raises(CredentialsUnavailable) as error:
        request_with_rotation(pool,lambda *_:next(responses),timeout_seconds=5,
            rotate_on_rate_limit=True,monotonic=clock)
    assert not isinstance(error.value,ProviderAuthenticationError)
    clock.now=11
    assert pool.acquire().reference=="KEY_B"

def test_all_auth_rejections_and_hard_quota_remain_terminal(tmp_path):
    clock=Clock();pool=CredentialPool(("KEY_A","KEY_B"),state_path=tmp_path/"pool.json",
        env={"KEY_A":"a","KEY_B":"b"},now=clock)
    with pytest.raises(ProviderAuthenticationError):
        request_with_rotation(pool,lambda *_:httpx.Response(401),timeout_seconds=5,monotonic=clock)
    with pytest.raises(ProviderAuthenticationError):pool.acquire()
    other=CredentialPool(("KEY_A",),state_path=tmp_path/"other.json",env={"KEY_A":"a"},now=clock)
    with pytest.raises(ProviderHardQuotaError):
        request_with_rotation(other,lambda *_:httpx.Response(402),timeout_seconds=5,monotonic=clock)
    with pytest.raises(ProviderHardQuotaError):other.acquire()

@pytest.mark.parametrize("protocol",["openai_chat","systemone_fixed"])
def test_actual_transport_uses_bounded_http_and_preserves_protocol(monkeypatch,protocol):
    from trading_gym.agent.providers import ProviderConfig,ProviderKind
    from trading_gym.intelligence.llm_backend import httpx_transport
    from trading_gym.intelligence.systemone_native_backend import httpx_systemone_transport
    real_client=httpx.Client;requests=[]
    payload={"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]} if protocol=="openai_chat" else {"answers":{}}
    def handler(request):
        requests.append(request)
        return httpx.Response(200,stream=Chunks([json.dumps(payload).encode()]))
    monkeypatch.setattr(httpx,"Client",lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    config=ProviderConfig(provider_id="bounded-test",kind=ProviderKind.LOCAL,display_name="test",
        model="test",base_url="http://127.0.0.1:12345",wire_protocol=protocol,
        metadata={"supports_json_schema":True})
    if protocol=="openai_chat":
        assert httpx_transport(config)(config.base_url,[]).content=="{}"
        sent=json.loads(requests[0].content)
        assert sent["response_format"]["json_schema"]["strict"] is True
        assert requests[0].url.path.endswith("chat/completions")
    else:
        assert httpx_systemone_transport(config)({"questions":{}})==payload
        assert requests[0].url.path.endswith("systemone")
    assert requests[0].headers["accept-encoding"]=="identity"
