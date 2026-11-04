"""Korean acoustic alignment. All inference stays on this computer."""
import re
import wave
from pathlib import Path
import numpy as np

MODEL = 'kresnik/wav2vec2-large-xlsr-korean'
REVISION = '629c9a3501c10ba128bf3fa1eebb12af3be03f61'


def ctc_path(emission, tokens, blank):
    """Viterbi CTC alignment with explicit blank states and repeat constraints."""
    if not tokens or len(emission) < len(tokens):
        raise ValueError('음성 길이에 비해 정렬할 대본이 너무 깁니다.')
    states = np.full(2 * len(tokens) + 1, blank, dtype=int)
    states[1::2] = tokens
    skip = np.zeros(len(states), dtype=bool)
    skip[2:] = (states[2:] != blank) & (states[2:] != states[:-2])
    score = np.full(len(states), -np.inf)
    score[0] = emission[0, blank]
    score[1] = emission[0, tokens[0]]
    pointers = np.zeros((len(emission), len(states)), dtype=np.uint8)
    for t in range(1, len(emission)):
        advance = np.r_[-np.inf, score[:-1]]
        jump = np.r_[-np.inf, -np.inf, score[:-2]]
        jump[~skip] = -np.inf
        choices = np.stack((score, advance, jump))
        pointers[t] = choices.argmax(axis=0)
        score = choices.max(axis=0) + emission[t, states]
    state = len(states) - 1 if score[-1] >= score[-2] else len(states) - 2
    if not np.isfinite(score[state]):
        raise ValueError('대본을 음성에 정렬하지 못했습니다.')
    spans = [[] for _ in tokens]
    for t in range(len(emission)-1, -1, -1):
        if state % 2: spans[state//2].append(t)
        state -= int(pointers[t, state])
    if any(not s for s in spans):
        raise ValueError('일부 글자의 발화 위치를 찾지 못했습니다.')
    return [(min(s), max(s)+1, float(np.exp(emission[s, token]).mean())) for s, token in zip(spans, tokens)]


def normalize_char(char):
    # Digits are pronounced as Sino-Korean here. The review UI must expose
    # unsupported/ambiguous spelling rather than silently promising certainty.
    if char.isascii() and char.isdigit():
        return '영일이삼사오육칠팔구'[int(char)]
    return char.lower()


def align_emissions(emission, text, vocab, offset=0, step=.02, pronunciations=None):
    labels, indices = [], []
    unsupported = set()
    nonblank = [v for k,v in vocab.items() if k not in ('[PAD]', '[UNK]', '|', '<s>', '</s>')]
    wildcard = emission.shape[1]
    emission = np.column_stack((emission, emission[:, nonblank].max(axis=1)))
    for i, char in enumerate(text):
        normalized = '|' if char.isspace() else (pronunciations or {}).get(char, normalize_char(char))
        for c in normalized:
            if c in vocab:
                labels.append(vocab[c]); indices.append(i)
            elif c.isalnum():
                labels.append(wildcard); indices.append(i); unsupported.add(i)
    spans = ctc_path(emission, labels, vocab['[PAD]'])
    mapped = {}
    for i, (a,b,score) in zip(indices,spans):
        if i not in mapped: mapped[i] = {'start':round(offset+a*step,4), 'score':score}
        mapped[i]['end'] = round(offset+b*step,4)
        mapped[i]['score'] = min(mapped[i]['score'],score) if i not in unsupported else 0
    words = []
    for match in re.finditer(r'\s*\S+', text):
        timing = [mapped[i] for i in range(match.start(),match.end()) if i in mapped and not text[i].isspace()]
        if not timing:
            raise ValueError('발화 위치를 찾을 수 없는 구절이 있습니다.')
        characters = []
        for i in range(match.start(),match.end()):
            if text[i].isspace(): continue
            if i in mapped: characters.append({'char':text[i], **mapped[i]})
            else:
                at = characters[-1]['end'] if characters else timing[0]['start']
                characters.append({'char':text[i],'start':at,'end':at,'score':1})
        words.append({'word':match.group(), 'start':timing[0]['start'], 'end':timing[-1]['end'],
                      'score': min(x['score'] for x in timing), 'chars':characters})
    return words


def align_audio(audio_path, seed_words, cache_dir, progress=None, device=None):
    """Bound memory for long recordings; keep context on either side of each chunk."""
    import torch
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
    from huggingface_hub import snapshot_download
    files = ['config.json','preprocessor_config.json','tokenizer_config.json','special_tokens_map.json','vocab.json','model.safetensors']
    try:
        model_path = snapshot_download(MODEL,revision=REVISION,cache_dir=str(cache_dir),local_files_only=True,allow_patterns=files)
        if not any((Path(model_path)/name).exists() for name in ('model.safetensors','pytorch_model.bin')):
            raise OSError('Model download incomplete')
    except OSError:
        model_path = snapshot_download(MODEL,revision=REVISION,cache_dir=str(cache_dir),
            allow_patterns=files)
    processor = Wav2Vec2Processor.from_pretrained(model_path,local_files_only=True)
    model = Wav2Vec2ForCTC.from_pretrained(model_path,local_files_only=True).eval()
    device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
    model.to(device)
    if device == 'cpu': torch.set_num_threads(min(4, torch.get_num_threads()))
    with wave.open(str(audio_path)) as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float32)/32768
    duration = len(samples)/16000
    vocab = processor.tokenizer.get_vocab()
    # Actual model feature stride, not duration / frame count (which drifts).
    step = float(np.prod(model.config.conv_stride))/16000
    batches = []
    if duration <= 45:
        batches = [(0,len(seed_words))]
    else:
        a = 0
        while a < len(seed_words):
            b = a+1
            while b < len(seed_words) and seed_words[b]['end']-seed_words[a]['start'] < 24: b += 1
            batches.append((a,b)); a = b
    result = []
    for batch_index,(a,b) in enumerate(batches):
        left,right = max(0,a-2), min(len(seed_words),b+2)
        start = 0 if left == 0 else max(0,seed_words[left]['start']-1)
        end = duration if right == len(seed_words) else min(duration,seed_words[right-1]['end']+1)
        if len(batches)==1: start,end=0,duration
        piece = samples[int(start*16000):int(end*16000)]
        with torch.inference_mode():
            inputs = processor(piece,sampling_rate=16000,return_tensors='pt').to(device)
            emission = model(**inputs).logits[0].log_softmax(-1).cpu().numpy()
        # Normalize only whitespace. User text and numeric spelling stay intact.
        context = seed_words[left:right]
        text = ' '.join(w['word'].strip() for w in context)
        aligned = align_emissions(emission,text,vocab,offset=start,step=step)
        if len(aligned) != len(context):
            raise ValueError('정밀 정렬의 단어 수가 달라 적용하지 않았습니다.')
        for original, timed in zip(context[a-left:b-left],aligned[a-left:b-left]):
            timed['word'] = original['word']
            result.append(timed)
        if progress: progress(round((batch_index+1)/len(batches)*100))
    return result
