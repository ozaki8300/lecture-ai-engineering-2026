"""Execute the teaching notebook's original cells, with isolated local output paths."""
import argparse
import contextlib
import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

LAB = Path(__file__).resolve().parents[1]
NOTEBOOK = LAB.parent / 'lecture-ai-engineering-2026-day02/notebooks/lora_format_tuning.ipynb'
parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['implementation'], default='implementation')
parser.add_argument('--override', help='JSON object: homework lora_r, lr or num_train_epochs only')
args = parser.parse_args()
if Path(sys.prefix).resolve() != (Path.home() / 'day02_lora_lab/venv').resolve():
    raise RuntimeError('Use the dedicated WSL ~/day02_lora_lab/venv')
os.environ.setdefault('HF_HOME', str(Path.home() / 'day02_lora_lab/cache/huggingface'))
os.environ.setdefault('TORCH_HOME', str(Path.home() / 'day02_lora_lab/cache/torch'))
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
overrides = json.loads(args.override or '{}')
if overrides and (args.mode != 'homework' or len(overrides) != 1 or not set(overrides) <= {'lora_r', 'lr', 'num_train_epochs'}):
    raise ValueError('Homework changes exactly one of lora_r/lr/num_train_epochs')
run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + args.mode
work = LAB / 'runs' / run_id
work.mkdir(exist_ok=False)
dataset_path = LAB / 'configs/implementation_ai_policy.json'
dataset_bytes = dataset_path.read_bytes()
dataset = json.loads(dataset_bytes)
(work / 'implementation_ai_policy.json').write_bytes(dataset_bytes)
(work / 'runner.py').write_bytes(Path(__file__).read_bytes())
notebook_bytes = NOTEBOOK.read_bytes()
nb = json.loads(notebook_bytes)
(work / 'source.ipynb').write_bytes(notebook_bytes)
status = dict(run_id=run_id, mode=args.mode, state='running', notebook_sha256=hashlib.sha256(notebook_bytes).hexdigest(), overrides=overrides)
def save(name, value):
    (work / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
save('status.json', status)
print(f'RUN_ID={run_id}', flush=True)
class Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, text):
        for stream in self.streams: stream.write(text); stream.flush()
    def flush(self):
        for stream in self.streams: stream.flush()
ns = {'__name__': '__main__', 'display': lambda value: print(value.to_string() if hasattr(value, 'to_string') else value)}
try:
    with (work / 'execution.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(Tee(sys.stdout, log)), contextlib.redirect_stderr(Tee(sys.stderr, log)):
        import torch
        torch.set_num_threads(min(8, os.cpu_count() or 1))
        if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; no CPU fallback for this run')
        versions = {name: importlib.metadata.version(name) for name in ['torch','transformers','peft','trl','accelerate','datasets','pandas']}
        env = dict(python=sys.version, executable=sys.executable, prefix=sys.prefix, platform=platform.platform(), versions=versions, cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(), gpu_memory=torch.cuda.mem_get_info(), nvidia_smi=subprocess.check_output(['nvidia-smi'], text=True))
        save('environment.json', env)
        (work / 'pip-freeze.txt').write_text(subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True))
        for name in ['transformers','peft','trl','accelerate','datasets','torch']:
            ns[name] = __import__(name)
        indices = [5, 7, 9] if args.mode == 'smoke' else [5, 7, 9, 10, 12, 15, 17, 19]
        for i in indices:
            source = ''.join(nb['cells'][i]['source'])
            if i == 7:
                source = "\n".join(k + " = " + repr(dataset[k]) for k in ['FAQ','GROUNDED','UNGROUNDED','TEST_EXTRA_GROUNDED','TEST_EXTRA_UNGROUNDED']) + "\nFAQ_TEXT = " + repr(chr(10).join('- ' + v for v in dataset['FAQ'].values())) + "\n" + source[source.index('UNGROUNDED_ANSWER ='):]
            if i == 17:
                source = 'KNOWLEDGE_QUESTIONS = ' + repr(dataset['KNOWLEDGE_QUESTIONS']) + '\n' + source[source.index('def classify'):]
                source = source.replace('q in gold', "q in " + repr(dataset['KNOWLEDGE_GROUNDED']))
            if i == 5:
                source = source.replace('"/content/qlora_lab"', repr(str(work))).replace('"/content/drive/MyDrive/aie2026/qlora_lab"', repr(str(work / 'unused_drive')))
            print(f'\n=== notebook cell {i} ===', flush=True)
            (work / f'cell_{i}.py').write_text(source, encoding='utf-8')
            exec(compile(source, f'{NOTEBOOK}:cell_{i}', 'exec'), ns)
            if i == 5:
                ns['CONFIG'].update(overrides)
                save('experiment_changes.json', dict(topic='fictional corporate AI policy', config_overrides=overrides, dataset_sha256=hashlib.sha256(dataset_bytes).hexdigest(), knowledge_membership_fix=True))
                save('config.json', ns['CONFIG'])
                save('condition_differences.json', dict(hardware='RTX 3070 Ti / WSL Ubuntu instead of Colab T4', python=platform.python_version(), torch=versions['torch'], output_paths='Run ID directory; no Google Drive', overrides=overrides, quantization=False, dtype='float32', amp=False, batch_size=4, gradient_accumulation_steps=2))
            if i == 7:
                assert [len(ns[k]) for k in ['train','valid','test']] == [14,7,21]
                (work / 'valid.jsonl').write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in ns['valid']), encoding='utf-8')
            if i == 9:
                save('model_smoke.json', dict(device=str(ns['model'].device), dtype=str(ns['model'].dtype), memory_gb=ns['BASE_MEMORY_GB'], free_total_bytes=torch.cuda.mem_get_info(), generated=ns['generate'](ns['model'], ns['test'][0])))
            if i == 12:
                save('training_history.json', ns['trainer'].state.log_history)
                ns['trainer'].state.save_to_json(str(work / 'trainer_state.json'))
        if args.mode != 'smoke':
            save('comparison.json', dict(before=ns['before_summary'], after=ns['after_summary'], regressed=ns['regressed']))
        status['state'] = 'completed'
except BaseException as error:
    status.update(state='failed', error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
    save('status.json', status)
    traceback.print_exc()
    raise
finally:
    status['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save('status.json', status)
