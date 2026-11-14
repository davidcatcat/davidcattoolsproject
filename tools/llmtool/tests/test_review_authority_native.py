"""Darwin-only T1 fake concurrency/crash proof; no production/provider egress."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from dataclasses import replace

from llmtool import authorization_ledger as l,authorization_policy as p
from llmtool.providers.cli_lifecycle import terminate
from test_review_authority import Harness
import review_trust_fixture


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()

CHILD=r'''
import json,os,sys,sqlite3
from pathlib import Path
spec=json.loads(sys.stdin.readline())
# #1821: import llmtool from the tree under test, also outside the canonical checkout.
sys.path.insert(0,spec['source_root'])
from llmtool import authorization_ledger as l,authorization_policy as p
from llmtool.review_authority import ProductionReviewIssuer
from llmtool.task_review import ReviewCallBinding
from llmtool.action_contract import TaskBinding
import llmtool
assert str(Path(llmtool.__file__).resolve()).startswith(spec['source_root']+'/')
class NativeFakeIssuer(ProductionReviewIssuer):
    _capability='approved-test-host'
    def __init__(self):pass
    def vouches_for(self,digest):return True
    def matches(self,call,observed):return observed.attested
    def daily_cap(self,digest):return spec['cap']
raw=spec['receipt']['binding']['call']
for k in ('ready_task','acceptance_task'):raw[k]=TaskBinding(**raw[k])
raw['criterion_map']=tuple(tuple(item) for item in raw['criterion_map'])
raw['author_agents']=tuple(raw['author_agents'])
b=p.ProviderReviewBinding(ReviewCallBinding(**raw))
receipt=l.Receipt(b,spec['receipt']['issued_at'],spec['receipt']['expires_at'],spec['receipt']['nonce'])
o=p.ProviderObservedState(b.digest,b.call.source_sha,b.call.observation_digest,True)
if spec['crash']=='before_commit':
    class Crash(sqlite3.Connection):
        consumed=False
        def execute(self,sql,*args):
            if sql.startswith("UPDATE receipts SET status='CONSUMED'"):self.consumed=True
            if sql=='COMMIT' and self.consumed:os._exit(17)
            return super().execute(sql,*args)
    connect=sqlite3.connect
    sqlite3.connect=lambda *args,**kwargs:connect(*args,factory=Crash,**kwargs)
with l.ReceiptLedger.open(spec['ledger'],clock=lambda:spec['now'],kind='PROVIDER_REVIEW',
                         creation_nonce=spec['creation_nonce']) as ledger:
    if sys.stdin.readline() != 'go\n': os._exit(3)
    try:
        ok=ledger.consume_provider(receipt,issuer=NativeFakeIssuer(),observed=o)
        if not ok:print('CAP_BLOCKED');sys.exit(0)
        if spec['crash']=='before_spawn':os._exit(17)
        with open(spec['effects'],'a') as effect:
            effect.write(receipt.nonce+'\n');effect.flush();os.fsync(effect.fileno())
        if spec['crash']=='after_response':os._exit(17)
        assert ledger.finalize_provider(receipt,'EFFECT_DONE')
        print('FAKE_EFFECT_DONE')
    except l.LedgerError as exc:print(exc.code.value)
'''


@unittest.skipUnless(sys.platform=='darwin','T1 requires actual Darwin; not Linux/native PASS')
class NativeProviderTests(unittest.TestCase):
    def setUp(self):
        from test_authorization_ledger import ProviderLedgerTests
        # Reuse that synthetic mechanics setup, without running its test body.
        setup=ProviderLedgerTests();setup.setUp()
        self.addCleanup(setup.doCleanups)
        self.h,self.issuer,self.helper=setup.h,setup.issuer,setup
        self.children=[];self.addCleanup(self.stop_children)
        self.effects=self.h.hostdir/'fake-effect-trace'

    def stop_children(self):
        for child in self.children:
            if child.returncode is None:terminate(child)
            for stream in (child.stdin,child.stdout,child.stderr):
                if stream is not None and not stream.closed:stream.close()

    def start(self,receipt,crash='',cap=1):
        spec=dict(receipt=receipt.to_dict(),ledger=str(self.h.ledger_path),now=self.h.clock.utc(),
            creation_nonce=self.h.ledger.creation_nonce,cap=cap,crash=crash,effects=str(self.effects),
            source_root=str(Path(__file__).resolve().parents[1]))
        env=dict(PATH='/usr/bin:/bin',HOME=str(self.h.auth),PYTHONDONTWRITEBYTECODE='1')
        child=subprocess.Popen([sys.executable,'-B','-I','-c',CHILD],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True,
            cwd=self.h.hostdir)
        self.children.append(child)
        child.stdin.write(json.dumps(spec)+'\n');child.stdin.flush()
        return child

    def finish(self,children):
        for child in children:child.stdin.write('go\n');child.stdin.flush()
        results=[]
        for child in children:
            try:
                out,err=child.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                terminate(child)  # Only this owned, unreaped child/process group.
                raise
            self.assertEqual(err,'');results.append((child.returncode,out.strip()))
        return results

    def test_same_receipt_race_one_fake_effect(self):
        receipt,o=self.helper.issue()
        results=self.finish([self.start(receipt),self.start(receipt)])
        self.assertEqual(sorted(out for code,out in results),['FAKE_EFFECT_DONE','NOT_ALLOWED'])
        self.assertEqual(len(self.effects.read_text().splitlines()),1)
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_DONE')

    def test_distinct_receipts_race_atomic_daily_cap(self):
        first,o1=self.helper.issue('1');second,o2=self.helper.issue('2')
        results=self.finish([self.start(first),self.start(second)])
        self.assertEqual(sorted(out for code,out in results),['CAP_BLOCKED','FAKE_EFFECT_DONE'])
        self.assertEqual(len(self.effects.read_text().splitlines()),1)
        statuses=[self.h.ledger.status(r.nonce) for r in (first,second)]
        self.assertEqual(sorted(statuses),['EFFECT_DONE','ISSUED'])

    def test_crash_before_commit_rolls_back_and_retries_unused_receipt(self):
        receipt,o=self.helper.issue()
        self.assertEqual(self.finish([self.start(receipt,'before_commit')]),[(17,'')])
        self.assertFalse(self.effects.exists())
        self.assertEqual(self.h.ledger.status(receipt.nonce),'ISSUED')
        self.assertEqual(self.finish([self.start(receipt)]),[(0,'FAKE_EFFECT_DONE')])
        self.assertEqual(len(self.effects.read_text().splitlines()),1)

    def test_committed_crash_before_spawn_unknown_no_retry(self):
        receipt,o=self.helper.issue()
        self.assertEqual(self.finish([self.start(receipt,'before_spawn')]),[(17,'')])
        self.assertFalse(self.effects.exists());self.assertEqual(self.h.ledger.recover(),1)
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
        self.assertEqual(self.finish([self.start(receipt)]),[(0,'NOT_ALLOWED')])
        next_receipt,o2=self.helper.issue('2')
        self.assertEqual(self.finish([self.start(next_receipt)]),[(0,'CAP_BLOCKED')])

    def test_crash_after_response_recovery_preserves_day_charge(self):
        receipt,o=self.helper.issue()
        self.assertEqual(self.finish([self.start(receipt,'after_response')]),[(17,'')])
        charge=self.h.ledger._db.execute('SELECT consumed_at,consumed_day FROM receipts').fetchone()
        self.assertEqual(self.h.ledger.recover(),1)
        self.assertEqual(charge,self.h.ledger._db.execute('SELECT consumed_at,consumed_day FROM receipts').fetchone())
        self.assertEqual(self.finish([self.start(receipt)]),[(0,'NOT_ALLOWED')])
        self.assertEqual(len(self.effects.read_text().splitlines()),1)


if __name__=='__main__':unittest.main()
