# The Distillation Decision Tree
## 5 Questions to Pick the Right Student Model Architecture

**Free checklist from the author of "Scalable Knowledge Distillation Playbook"**

---

## Before you start distilling, answer these:

### 1. What's your latency budget?
- **<50ms per token** → Go 7B or smaller, quantize to GGUF Q4
- **50-200ms acceptable** → 13B-30B range, FP16 or Q8
- **Batch inference OK** → Can go larger, optimize for throughput not latency

### 2. What teacher are you distilling from?
- **LLaMA 2/3 family** → Use same tokenizer, layer alignment is natural
- **Mistral/Mixtral** → Watch for MoE routing distillation (special case)
- **Qwen family** → Larger vocab, adjust embedding layer handling
- **Other** → Tokenizer mismatch = biggest failure mode, handle carefully

### 3. What's your target domain?
- **General purpose** → Full model distillation, all layers
- **Code generation** → Focus on later layers (reasoning-heavy)
- **Domain-specific** → Consider LoRA on base + distillation hybrid

### 4. How much compute do you have?
- **1x A100/H100** → Single-stage distillation, batch carefully
- **4-8 GPUs** → Multi-stage (layers progressively), better results
- **3090/4090 consumer** → Quantize teacher to 4-bit, distill in chunks

### 5. What evaluation matters?
- **Benchmark scores** → Run MMLU/GSM8K on every checkpoint
- **Human eval** → Build a small test suite of real prompts
- **Latency/throughput** → Profile before AND after distillation
- **All of the above** → You need our full playbook (linked below)

---

## Common Pitfalls (that waste weeks):

❌ **Temperature too low** → Student copies teacher verbatim, no learning
✅ Use T=2.0-4.0, anneal down over training

❌ **No layer alignment check** → Architectures differ in subtle ways
✅ Print layer shapes, verify before training starts

❌ **Single checkpoint eval** → Cherry-picking kills you in production
✅ Track metrics every 500 steps, pick by validation not test

❌ **Forgetting the tokenizer** → Mismatch = silent quality degradation
✅ Always verify tokenization of test prompts pre/post distillation

---

**Want the full implementation guide?**
The "Scalable Knowledge Distillation Playbook" covers all of this in depth with code examples, benchmarks, and production configs.

👉 [Get the playbook](https://straughterg.gumroad.com/l/distillation-playbook)

---

*Built by @StraughterG — AI Distillation Architect*
*GitHub: github.com/jmanhype*
