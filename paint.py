from rq.notebooks.notebook_utils import TextEncoder, load_model, get_generated_images_by_texts
import numpy as np
import itertools
import requests
from konlpy.tag import Okt
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer

import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import transformers
import yaml
import torch
import torchvision
import clip
import torch.nn.functional as F
import time

import os
import json
os.environ["TOKENIZERS_PARALLELISM"] = "false"

secret_file = 'secrets.json'

with open(secret_file) as f:
    secrets = json.loads(f.read())


def get_secret(setting, secrets=secrets):
    try:
        return secrets[setting]
    except KeyError:
        return "error!"


JVM_PATH_TEM = '/Library/Java/JavaVirtualMachines/zulu-15.jdk/Contents/Home/bin/java'

doc = "내가 제일 좋아하는 음식은 햄버거이다. 그래서 오늘은 햄버거가게에 가서 햄버거를 먹었다. 감자튀김도 들어있는 햄버거세트로 먹었다. 정말 배부르고 맛있었다. 매일 먹고싶지만 그러면 체중이 늘어나겠지? 그래도 매일 매일 먹고싶다!"


def paint(doc):
    start = time.time()
    okt = Okt(jvmpath=JVM_PATH_TEM)
    tokenized_doc = okt.pos(doc)
    tokenized_nouns = ' '.join([word[0]
                                for word in tokenized_doc if word[1] == 'Noun'])

    print('품사 태깅 10개만 출력 :', tokenized_doc[:10])
    print('명사 추출 :', tokenized_nouns)

    n_gram_range = (2, 3)

    # CountVectorizer를 사용하는 이유 : n_gram_range의 인자를 사용하면 쉽게 n-gram을 추출할 수 있음
    count = CountVectorizer(ngram_range=n_gram_range).fit([tokenized_nouns])
    candidates = count.get_feature_names_out()

    print('trigram 개수 :', len(candidates))
    print('trigram 다섯개만 출력 :', candidates[:5])

    model = SentenceTransformer(
        'sentence-transformers/xlm-r-100langs-bert-base-nli-stsb-mean-tokens')
    doc_embedding = model.encode([doc])
    candidate_embeddings = model.encode(candidates)

    top_n = 5
    distances = cosine_similarity(doc_embedding, candidate_embeddings)
    keywords = [candidates[index] for index in distances.argsort()[0][-top_n:]]
    print(keywords)

    max_sum_sim(doc_embedding, candidate_embeddings,
                candidates, top_n=5, nr_candidates=5)
    keyword = max_sum_sim(doc_embedding, candidate_embeddings,
                          candidates, top_n=5, nr_candidates=5)[0]
    trans = get_translate(keyword)
    print(trans)
    input_template = "a Picture containing a "+trans

    vqvae_path = 'model/cc3m/stage1/model.pt'
    model_path = 'model/cc3m/stage2/model.pt'

    # load stage 1 model: RQ-VAE
    model_vqvae, _ = load_model(vqvae_path)

    # load stage 2 model: RQ-Transformer
    model_ar, config = load_model(model_path, ema=False)

    # GPU
    model_ar = model_ar.eval()
    print("완료1")
    model_vqvae = model_vqvae.eval()
    print("완료2")

    # CLIP
    model_clip, preprocess_clip = clip.load("ViT-B/32", device='cpu')
    print("완료3")
    model_clip = model_clip.eval()
    print("완료4")

    # prepare text encoder to tokenize natual languages
    text_encoder = TextEncoder(tokenizer_name=config.dataset.txt_tok_name,
                               context_length=config.dataset.context_length)

    text_prompts = input_template  # your own text
    num_samples = 64
    temperature = 1.0
    top_k = 1024
    top_p = 0.95

    print("완료5")
    pixels = get_generated_images_by_texts(model_ar,
                                           model_vqvae,
                                           text_encoder,
                                           model_clip,
                                           preprocess_clip,
                                           text_prompts,
                                           num_samples,
                                           temperature,
                                           top_k,
                                           top_p,
                                           )

    print("완료6")
    num_visualize_samples = 12
    images = [pixel.cpu().numpy() * 0.5 + 0.5 for pixel in pixels]
    images = torch.from_numpy(np.array(images[:num_visualize_samples]))
    images = torch.clamp(images, 0, 1)
    grid = torchvision.utils.make_grid(images, nrow=4)

    img = Image.fromarray(np.uint8(grid.numpy().transpose([1, 2, 0])*255))
    print(type(img))
    print(img)
    return img
    # imgName = "test"
    # img.save('img/'+imgName+'.jpg', 'JPEG')
    # end = time.time()
    # print(f"{end - start:.5f} sec")
    # return "finishh"


def max_sum_sim(doc_embedding, candidate_embeddings, candidates, top_n, nr_candidates):
    # 문서와 각 키워드들 간의 유사도
    distances = cosine_similarity(doc_embedding, candidate_embeddings)

    # 각 키워드들 간의 유사도
    distances_candidates = cosine_similarity(candidate_embeddings,
                                             candidate_embeddings)

    # 코사인 유사도에 기반하여 키워드들 중 상위 top_n개의 단어를 pick.
    words_idx = list(distances.argsort()[0][-nr_candidates:])
    words_vals = [candidates[index] for index in words_idx]
    distances_candidates = distances_candidates[np.ix_(words_idx, words_idx)]

    # 각 키워드들 중에서 가장 덜 유사한 키워드들간의 조합을 계산
    min_sim = np.inf
    candidate = None
    for combination in itertools.combinations(range(len(words_idx)), top_n):
        sim = sum([distances_candidates[i][j]
                  for i in combination for j in combination if i != j])
        if sim < min_sim:
            candidate = combination
            min_sim = sim

    return [words_vals[idx] for idx in candidate]


def get_translate(text):
    client_id = get_secret("PAPAGO_API_CLIENT_ID")  # <-- client_id 기입
    client_secret = get_secret("PAPAGO_API_SECRET_KEY")  # <-- client_secret 기입

    data = {'text': text,
            'source': 'ko',
            'target': 'en'}

    url = "https://openapi.naver.com/v1/papago/n2mt"

    header = {"X-Naver-Client-Id": client_id,
              "X-Naver-Client-Secret": client_secret}

    response = requests.post(url, headers=header, data=data)
    rescode = response.status_code

    if(rescode == 200):
        send_data = response.json()
        trans_data = (send_data['message']['result']['translatedText'])
        return trans_data
    else:
        print("Error Code:", rescode)


if __name__ == '__main__':
    paint()
