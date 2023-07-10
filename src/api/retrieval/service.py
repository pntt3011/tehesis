import json
import pickle
import sys
from pathlib import Path

import os
import gdown
import zipfile
import numpy as np
import PIL.Image
import torch

FILE = Path(__file__).resolve()
ROOT = FILE.parent  # YOLOv5 root directory
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH


from model.clip4cir import CLIP4CirModule
from model.outfits_transformer import OutfitsTransformerModule
from search.search_engine import SearchEngine


def preload(device):
    pretrained_dir = ROOT / "pretrained"
    data_dir = ROOT / "dataset"
    content = {}

    if not (pretrained_dir / "clip_ft.pt").exists():
        pretrained_dir.mkdir(parents=True, exist_ok=True)
        out = str(pretrained_dir / "pretrained.zip")
        gdown.download("https://drive.google.com/uc?id=1qRP24WngO52MlxXVHOVzhXHql9GlsaUg", out)
        with zipfile.ZipFile(out, 'r') as zip_ref:
            zip_ref.extractall(str(pretrained_dir))
        os.remove(out)   

    # Load models
    content["models"] = {}
    content["models"]["clip4cir"] = CLIP4CirModule(
        pretrained_dir / "clip.pt",
        pretrained_dir / "clip_ft.pt",
        pretrained_dir / "combiner.pt",
        device,
    )
    content["models"]["outfits_transformer"] = OutfitsTransformerModule(
        pretrained_dir / "outfits_transformer.pt", device
    )

    # Load item names
    with open(data_dir / "polyvore_index_names.pkl", "rb") as f:
        content["index_names"] = np.array(pickle.load(f))

    # Load item metadatas
    content["index_metadatas"] = {}
    with open(data_dir / "polyvore_item_metadata.json") as f:
        data = json.load(f)

        for idx, name in enumerate(content["index_names"]):
            if name in data:
                category = data[name]["semantic_category"]
                content["index_metadatas"][idx] = {"category": category}

    # Load categories
    content["categories"] = [
        "all-body",
        "bags",
        "tops",
        "outerwear",
        "hats",
        "bottoms",
        "scarves",
        "jewellery",
        "accessories",
        "shoes",
        "sunglasses",
    ]

    # Load item embeddings and save to approximate searching
    content["search_engine"] = {}
    features = torch.nn.functional.normalize(
        torch.load(data_dir / "polyvore_index_embeddings.pt", map_location=device).type(torch.float32)
    ).cpu().detach().numpy()
    index_types = ["all", *content["categories"]]
    for type in index_types:
        if not SearchEngine.can_load(pretrained_dir, type):
            if type != "all":
                category_indices = np.array([i for i in content["index_metadatas"] if content["index_metadatas"][i]["category"] == type])
            else:
                category_indices = np.arange(len(features))
            category_features = features[category_indices]

            index = SearchEngine(category_indices, category_features)
            SearchEngine.save(index, pretrained_dir, type)
     
        content["search_engine"][type] = SearchEngine.load(pretrained_dir, type)

    return content


@torch.no_grad()
def tgir(image: PIL.Image.Image, caption: str, api_content: dict):
    return api_content["models"]["clip4cir"](image, caption)


@torch.no_grad()
def ocir(image: PIL.Image.Image, category: str, api_content: dict):
    embedding = api_content["models"]["clip4cir"].encode_image(image)

    if category not in api_content["categories"]:
        return {i: [] for i in api_content["categories"]}

    idx = api_content["categories"].index(category)
    mask = torch.full((len(api_content["categories"]),), False)
    mask[idx] = True

    output = api_content["models"]["outfits_transformer"](embedding, mask)
    return dict(zip(api_content["categories"], output))


def query_top_k_items(embedding, category, top_k, api_content: dict):
    if category == None:
        category = "all"
    
    search_engine = api_content["search_engine"][category]
    results = search_engine.run(embedding, top_k)
    return results


def get_category(item_index, api_content: dict):
    if item_index in api_content["index_metadatas"]:
        return api_content["index_metadatas"][item_index]["category"]

    return None


def get_item_name(item_index, api_content: dict):
    return api_content["index_names"][item_index]
