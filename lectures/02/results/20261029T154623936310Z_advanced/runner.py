"""Day2 advanced: official glossary SFT versus a fixed lexical retriever."""
import os, sys, json, re, math, time, datetime, hashlib, subprocess, contextlib, traceback
from pathlib import Path
from collections import Counter

LAB=Path('/mnt/c/Users/ozaki/Desktop/AI_Engineering/day02_lora_lab')
REPO=LAB.parent/'lecture-ai-engineering-2026-day02'
assert Path(sys.prefix)==Path('/home/ozaki/day02_lora_lab/venv')
os.environ['HF_HOME']='/home/ozaki/day02_lora_lab/cache/huggingface'
os.environ['TOKENIZERS_PARALLELISM']='false'
rid=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_advanced'
run=LAB/'runs'/rid
for p in (LAB/'runs').glob('*/status.json'):
    assert json.loads(p.read_text())['state']!='running',str(p)
run.mkdir()
save=lambda n,v:(run/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
status={'run_id':rid,'mode':'advanced','state':'running'}
save('status.json',status)
print('RUN_ID='+rid,flush=True)
class Tee:
    def __init__(self,*streams): self.streams=streams
    def write(self,s):
        for f in self.streams:f.write(s);f.flush()
    def flush(self):
        for f in self.streams:f.flush()
SYSTEM='講座用語集に沿って、質問された用語を日本語で簡潔に説明してください。用語集にない語や不明な情報は推測せず「用語集に記載がないため確認が必要です」と答えてください。'
def normalize(x):return re.sub(r'\s+','',x).lower()
def grams(s):
    s=normalize(s); return Counter(s[i:i+2] for i in range(len(s)-1))
def rubric(raw,entry):
    text=normalize(raw)
    abstain=('確認' in text and any(x in text for x in ['必要','不明','記載','わから','分から']))
    if entry['kind']=='absent':return abstain
    return not abstain and all(any(normalize(x) in text for x in group) for group in entry['concepts'])
try:
  with (run/'execution.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(Tee(sys.stdout,log)),contextlib.redirect_stderr(Tee(sys.stderr,log)):
    glossary=json.loads((REPO/'data/glossary.json').read_text())
    terms=glossary['terms']; byid={x['id']:x for x in terms}
    (run/'glossary.json').write_bytes((REPO/'data/glossary.json').read_bytes())
    (run/'make_sft_dataset.py').write_bytes((REPO/'scripts/make_sft_dataset.py').read_bytes())
    (run/'runner.py').write_bytes(Path(__file__).read_bytes())
    official=subprocess.check_output([sys.executable,'-B',str(REPO/'scripts/make_sft_dataset.py')],text=True)
    (run/'glossary_sft_official.jsonl').write_text(official,encoding='utf-8')
    records=[json.loads(x) for x in official.splitlines()]
    assert len(records)==2*len(terms)
    train=[{'messages':[{'role':'system','content':SYSTEM}]+x['messages']} for x in records[::2]]
    valid=[{'messages':[{'role':'system','content':SYSTEM}]+x['messages']} for x in records[1::2]]
    for name,rows in [('train',train),('valid',valid)]:
        (run/(name+'.jsonl')).write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows),encoding='utf-8')
    specs=[
      ('lora','元の重みを固定し、更新を小さな二つの行列で学ぶ方法を説明してください。',[['固定','凍結'],['行列'],['学習']]),
      ('qlora','重みを4ビット化してから低ランクの追加行列を学ぶ方法はどのようなものですか。',[['4ビット','4bit','4-bit','量子化'],['LoRA','低ランク'],['メモリ']]),
      ('quantization','モデルの重みのビット数を減らす技術にはどんな利点と注意点がありますか。',[['ビット'],['メモリ'],['精度']]),
      ('overfitting','練習データだけに適合して未知の入力で性能が落ちる状態と、その検出方法を説明してください。',[['学習データ','訓練データ'],['未知','新しい','汎化'],['検証']]),
      ('context-window','モデルが一回の呼び出しで処理できる入力と出力の合計量の上限を説明してください。',[['入力'],['出力'],['トークン'],['上限','最大']]),
      ('rate-limit','一定時間に呼び出せる量を超えると429エラーになる仕組みを説明してください。',[['時間'],['上限','制限'],['429']]),
      ('homework-levels','この講座の課題にはどんな難易度の区分があり、いくつ取り組めば完了しますか。',[['基礎'],['実装'],['発展'],['1つ','一つ','1レベル']]),
      ('answer-md','この講座の演習結果はどのファイルに書き、どのような手順で提出しますか。',[['answer.md'],['PR','プルリクエスト'],['main']])]
    test=[]
    for key,question,concepts in specs:
        term=byid[key]
        for kind,q in [('named',f'講座用語集における「{term["term"]}」の意味を教えてください。'),('paraphrase',question)]:
            test.append(dict(question=q,kind=kind,term_id=key,reference=term['definition'],concepts=concepts))
    for term in ['知識蒸留','DPO','勾配チェックポイント','フラッシュアテンション']:
        assert not any(normalize(term)==normalize(v) for t in terms for v in [t['term']]+t['aliases'])
        test.append(dict(question=f'講座用語集における「{term}」の意味を教えてください。',kind='absent',term_id=None,reference='用語集に記載がないため確認が必要です',concepts=[]))
    assert len({x['question'] for x in test})==20
    assert not set(x['question'] for x in test)&set(x['messages'][1]['content'] for x in train+valid)
    for x in test:assert rubric(x['reference'],x),x['term_id']
    save('evaluation_spec.json',test)
    # Fixed retriever: longest exact term/alias (>=2 chars), otherwise character-bigram TF-IDF cosine.
    docs=[grams(t['term']+' '+ ' '.join(t['aliases'])+' '+t['definition']) for t in terms]
    df=Counter(g for d in docs for g in d)
    idf={g:math.log((1+len(docs))/(1+n))+1 for g,n in df.items()}
    def vector(c):
        v={g:n*idf[g] for g,n in c.items() if g in idf}; norm=math.sqrt(sum(n*n for n in v.values()))
        return {g:n/norm for g,n in v.items()} if norm else {}
    vectors=[vector(d) for d in docs]
    def search(question):
        q=normalize(question); exact=[]
        for i,t in enumerate(terms):
            for alias in [t['term']]+t['aliases']:
                a=normalize(alias)
                if len(a)>=2 and a in q: exact.append((len(a),i))
        if exact:idx=max(exact)[1]; score=1.; method='exact_term_or_alias'
        else:
            v=vector(grams(question)); scores=[sum(n*d.get(g,0) for g,n in v.items()) for d in vectors]
            idx=max(range(len(scores)),key=scores.__getitem__);score=scores[idx];method='bigram_tfidf'
        if score<0.15:return '用語集に記載がないため確認が必要です',None,score,method
        return terms[idx]['definition'],terms[idx]['id'],score,method
    rows=[];start=time.perf_counter()
    for entry in test:
        raw,key,score,method=search(entry['question'])
        rows.append(dict(**entry,raw=raw,correct=rubric(raw,entry),retrieved_id=key,score=score,method=method))
    def summary(rows,seconds):return dict(n=len(rows),correct=sum(r['correct'] for r in rows),accuracy=sum(r['correct'] for r in rows)/len(rows),seconds=seconds,by_kind={k:dict(n=sum(r['kind']==k for r in rows),correct=sum(r['correct'] for r in rows if r['kind']==k)) for k in ['named','paraphrase','absent']})
    retrieval=dict(summary=summary(rows,time.perf_counter()-start),rows=rows)
    save('eval_retrieval.json',retrieval)
    import torch, transformers, peft, trl, datasets
    from transformers import AutoTokenizer,AutoModelForCausalLM
    from peft import LoraConfig
    from trl import SFTConfig,SFTTrainer
    from datasets import Dataset
    assert torch.cuda.is_available()
    torch.set_num_threads(8)
    cfg=dict(model='Qwen/Qwen3.5-0.8B',revision='2fc06364715b967f1860aea9cf38778875588b17',r=16,alpha=32,lr=2e-4,epochs=20,seed=42,max_length=512,max_new_tokens=192,batch=4,gradient_accumulation=2,dtype='float32',system=SYSTEM,retrieval_threshold=0.15,train_count=len(train),valid_count=len(valid))
    save('config.json',cfg)
    save('environment.json',dict(python=sys.version,versions={m.__name__:m.__version__ for m in [torch,transformers,peft,trl,datasets]},cuda=torch.version.cuda,gpu=torch.cuda.get_device_name()))
    (run/'pip-freeze.txt').write_text(subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True))
    tok=AutoTokenizer.from_pretrained(cfg['model'],revision=cfg['revision'])
    if tok.pad_token is None:tok.pad_token=tok.eos_token
    lengths=[len(tok.apply_chat_template(x['messages'],tokenize=True,enable_thinking=False)) for x in train+valid]
    assert max(lengths)<=512,max(lengths)
    save('dataset_validation.json',dict(train=len(train),valid=len(valid),test=len(test),max_tokens=max(lengths),overlap=False,official_rows=len(records)))
    model=AutoModelForCausalLM.from_pretrained(cfg['model'],revision=cfg['revision'],dtype=torch.float32).to('cuda')
    def evaluate(label):
        model.eval();model.config.use_cache=True
        rows=[];start=time.perf_counter()
        for i,entry in enumerate(test):
            messages=[dict(role='system',content=SYSTEM),dict(role='user',content=entry['question'])]
            prompt=tok.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
            inputs=tok(prompt,return_tensors='pt').to('cuda')
            with torch.no_grad():out=model.generate(**inputs,max_new_tokens=192,do_sample=False,pad_token_id=tok.pad_token_id)
            raw=tok.decode(out[0][inputs['input_ids'].shape[1]:],skip_special_tokens=True).strip()
            rows.append(dict(**entry,raw=raw,correct=rubric(raw,entry)))
            print(label,i+1,entry['kind'],rows[-1]['correct'],flush=True)
        obj=dict(summary=summary(rows,time.perf_counter()-start),rows=rows);save('eval_'+label+'.json',obj);return obj
    before=evaluate('before')
    model.config.use_cache=False
    lora=LoraConfig(r=16,lora_alpha=32,lora_dropout=0.05,bias='none',task_type='CAUSAL_LM',target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'])
    sft=SFTConfig(output_dir=str(run/'ckpt'),num_train_epochs=20,per_device_train_batch_size=4,gradient_accumulation_steps=2,learning_rate=2e-4,lr_scheduler_type='cosine',warmup_steps=6,logging_steps=10,save_strategy='no',fp16=False,bf16=False,max_length=512,packing=False,report_to='none',seed=42)
    trainer=SFTTrainer(model=model,args=sft,train_dataset=Dataset.from_list(train),eval_dataset=Dataset.from_list(valid),peft_config=lora,processing_class=tok)
    trainer.model.print_trainable_parameters()
    trainer.train()
    trainer.model.save_pretrained(run/'adapter');trainer.state.save_to_json(str(run/'trainer_state.json'))
    model=trainer.model
    after=evaluate('after')
    save('comparison.json',dict(before=before['summary'],after=after['summary'],retrieval=retrieval['summary']))
    status['state']='completed'
except BaseException as e:
    status.update(state='failed',error=str(e),traceback=traceback.format_exc());raise
finally:
    status['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();save('status.json',status)
