"""LoRA/QLoRA fine-tuning for several small local instruct models.

Examples:
  python scripts/train_models.py --train finetune.jsonl --models qwen_0_5b qwen_1_5b
  python scripts/train_models.py --train finetune.jsonl --models all --qlora

The model list and LoRA modules live in config/local_models.json.
"""
import argparse, json, math, os, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import load_json

p = argparse.ArgumentParser()
p.add_argument("--train", required=True)
p.add_argument("--models", nargs="+", default=["all"])
p.add_argument("--out", default="./adapters")
p.add_argument("--epochs", type=float, default=2.0)
p.add_argument("--lr", type=float, default=2e-4)
p.add_argument("--batch", type=int, default=1)
p.add_argument("--grad-accum", type=int, default=8)
p.add_argument("--eval-ratio", type=float, default=0.1)
p.add_argument("--seed", type=int, default=42)
p.add_argument("--qlora", action="store_true", help="Use 4-bit QLoRA when CUDA+bitsandbytes are available")
p.add_argument("--max-steps", type=int, default=-1)
p.add_argument("--device", default="auto", choices=["auto","cuda","mps","cpu"])
a = p.parse_args()

import torch
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM, Trainer, TrainingArguments
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

presets = load_json("local_models.json")
selected = list(presets) if a.models == ["all"] else a.models
rows = [json.loads(x) for x in Path(a.train).read_text(encoding="utf-8").splitlines() if x.strip()]
random.Random(a.seed).shuffle(rows)
cut = max(1, int(len(rows) * (1 - a.eval_ratio))) if len(rows) > 1 else len(rows)
train_rows, eval_rows = rows[:cut], rows[cut:] or rows[: min(1, len(rows))]

class ChatDataset(Dataset):
    def __init__(self, rows, tok, max_len):
        self.items = []
        for row in rows:
            msgs = row["messages"]
            prefix = tok.apply_chat_template(msgs[:2], tokenize=False, add_generation_prompt=True)
            answer = msgs[2]["content"] + (tok.eos_token or "")
            pids = tok(prefix, add_special_tokens=False, truncation=True, max_length=max_len)["input_ids"]
            full = tok(prefix + answer, add_special_tokens=False, truncation=True, max_length=max_len)["input_ids"]
            labels = [-100] * min(len(pids), len(full)) + full[len(pids):]
            labels = labels[: len(full)]
            self.items.append({"input_ids": torch.tensor(full, dtype=torch.long), "labels": torch.tensor(labels, dtype=torch.long), "attention_mask": torch.ones(len(full), dtype=torch.long)})
    def __len__(self): return len(self.items)
    def __getitem__(self, i): return self.items[i]

class Collator:
    def __init__(self, pad): self.pad = pad
    def __call__(self, batch):
        ids = pad_sequence([x["input_ids"] for x in batch], batch_first=True, padding_value=self.pad)
        labels = pad_sequence([x["labels"] for x in batch], batch_first=True, padding_value=-100)
        mask = pad_sequence([x["attention_mask"] for x in batch], batch_first=True, padding_value=0)
        return {"input_ids": ids, "labels": labels, "attention_mask": mask}

summary = []
for alias in selected:
    if alias not in presets:
        summary.append({"alias": alias, "status": "error", "error": "unknown model alias"}); continue
    cfg = presets[alias]
    model_id = cfg["model_id"]
    out = Path(a.out) / alias
    out.mkdir(parents=True, exist_ok=True)
    try:
        tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        tok.pad_token = tok.pad_token or tok.eos_token
        if a.device == "auto":
            if torch.cuda.is_available(): device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available(): device = "mps"
            else: device = "cpu"
        else:
            device = a.device
        kwargs = {"trust_remote_code": True}
        quantized = False
        if a.qlora and device == "cuda":
            try:
                from transformers import BitsAndBytesConfig
                kwargs["device_map"] = "auto"
                kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
                quantized = True
            except Exception:
                quantized = False
        elif a.qlora and device != "cuda":
            print(f"[{alias}] QLoRA requested but {device} has no supported bitsandbytes 4-bit path; using ordinary LoRA.")
        if not quantized and device == "cuda":
            kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            kwargs["device_map"] = "auto"
        elif not quantized and device == "mps":
            kwargs["torch_dtype"] = torch.float16
        model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
        if device == "mps" and not quantized:
            model = model.to("mps")
        elif device == "cpu" and not quantized:
            model = model.to("cpu")
        model.config.use_cache = False
        model.gradient_checkpointing_enable()
        if quantized:
            model = prepare_model_for_kbit_training(model)
        lora = LoraConfig(
            r=int(cfg.get("lora_r", 16)), lora_alpha=int(cfg.get("lora_alpha", 32)),
            lora_dropout=float(cfg.get("lora_dropout", 0.05)), bias="none", task_type="CAUSAL_LM",
            target_modules=cfg.get("target_modules"),
        )
        model = get_peft_model(model, lora)
        max_len = int(cfg.get("max_length", 4096))
        train_ds, eval_ds = ChatDataset(train_rows, tok, max_len), ChatDataset(eval_rows, tok, max_len)
        args = TrainingArguments(
            output_dir=str(out / "checkpoints"), num_train_epochs=a.epochs, learning_rate=a.lr,
            per_device_train_batch_size=a.batch, per_device_eval_batch_size=a.batch,
            gradient_accumulation_steps=a.grad_accum, logging_steps=5,
            save_strategy="epoch", eval_strategy="epoch" if len(eval_ds) else "no",
            report_to="none", bf16=(device == "cuda" and torch.cuda.is_bf16_supported()),
            fp16=(device == "cuda" and not torch.cuda.is_bf16_supported()),
            max_steps=a.max_steps, seed=a.seed, remove_unused_columns=False,
        )
        trainer = Trainer(model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds if len(eval_ds) else None, data_collator=Collator(tok.pad_token_id))
        result = trainer.train()
        model.save_pretrained(out)
        tok.save_pretrained(out)
        metrics = dict(result.metrics)
        if len(eval_ds): metrics.update({"eval": trainer.evaluate()})
        (out / "training_meta.json").write_text(json.dumps({"alias": alias, "model_id": model_id, "device": device, "quantized": quantized, "train_rows": len(train_ds), "eval_rows": len(eval_ds), "metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append({"alias": alias, "status": "ok", "out": str(out), "model_id": model_id})
        del trainer, model
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    except Exception as e:
        summary.append({"alias": alias, "status": "error", "model_id": model_id, "error": repr(e)})

Path(a.out).mkdir(parents=True, exist_ok=True)
(Path(a.out) / "training_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
