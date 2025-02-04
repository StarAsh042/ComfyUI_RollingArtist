import os
import random
import csv
import threading
from typing import List
import comfy.sd
import nodes

class RollingArtist:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "artist_count": ("INT", {"default": 5, "min": 1, "max": 10}),
                "artists_prefix": ("BOOLEAN", {"default": True}),
                "weight_min": ("FLOAT", {"default": 0.2, "min": 0.1, "max": 1.0, "step": 0.1}),
                "weight_max": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 2.0, "step": 0.1}),
                "weight_total": ("FLOAT", {"default": 3.0, "min": 1.0, "max": 20.0, "step": 1.0}),
                "seed": ("INT", {"default": 1234, "min": 0, "max": 4294967295}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "generate_artists"
    CATEGORY = "RollingArtist"
    OUTPUT_NODE = True

    def __init__(self):
        self.artists = self.load_artists()
        self.lock = threading.Lock()
        # 定义 Top 艺术家：CSV 中前 20%（至少 1 个）
        if self.artists:
            top_n = max(1, int(len(self.artists) * 0.2))
            self.top_artists = set(self.artists[:top_n])
            self.non_top_artists = [a for a in self.artists if a not in self.top_artists]
        else:
            self.top_artists = set()
            self.non_top_artists = []

    def load_artists(self) -> List[str]:
        csv_path = os.path.join(os.path.dirname(__file__), "danbooru_art_001.csv")
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                return [artist for row in reader for artist in row if artist]
        except Exception as e:
            print(f"Failed to load CSV: {e}")
            return []

    def generate_fixed_weights(self, count: int, weight_min: float, weight_max: float, weight_total: float, rng: random.Random) -> List[float]:
        """
        根据参数生成每个艺术家的权重，使权重之和在合法范围内。
        """
        n = count
        lower_bound = n * weight_min
        upper_bound = n * weight_max
        weight_total = max(lower_bound, min(weight_total, upper_bound))
        extra_total = round(weight_total - lower_bound, 1)
        weights = [weight_min] * n
        for i in range(n):
            allowed = round(min(weight_max - weight_min, extra_total), 1)
            extra = round(rng.uniform(0, allowed), 1)
            weights[i] += extra
            extra_total = round(extra_total - extra, 1)
        if extra_total > 0:
            for i in range(n):
                can_add = round(weight_max - weights[i], 1)
                addition = round(min(can_add, extra_total), 1)
                weights[i] += addition
                extra_total = round(extra_total - addition, 1)
                if extra_total <= 0:
                    break
        return [round(w, 1) for w in weights]

    def _get_random_artists(self, count: int, rng: random.Random) -> List[str]:
        """
        利用传入的随机对象对艺术家列表乱序后返回前 count 个。
        """
        lst = self.artists.copy()
        rng.shuffle(lst)
        return lst[:count]

    def _adjust_top_artists(self, artists: List[str], rng: random.Random) -> List[str]:
        """
        如果选中的艺术家中 Top 艺术家数量超过 3，则随机替换多余的 Top 艺术家，
        用非 Top 艺术家填充。
        """
        count_top = sum(1 for a in artists if a in self.top_artists)
        if count_top > 3:
            indices = [i for i, a in enumerate(artists) if a in self.top_artists]
            rng.shuffle(indices)
            need = count_top - 3
            available = list(set(self.non_top_artists) - set(artists))
            for i in indices:
                if need <= 0:
                    break
                if available:
                    artists[i] = rng.choice(available)
                    available.remove(artists[i])
                    need -= 1
                else:
                    break
        return artists

    def _generate_prompt(self, artist_order: List[str], rng_weight: random.Random, weight_min: float, weight_max: float, weight_total: float, prefix: str) -> str:
        weights = self.generate_fixed_weights(len(artist_order), weight_min, weight_max, weight_total, rng_weight)
        return ",".join(f"({prefix}{a}:{w})" for a, w in zip(artist_order, weights))

    def generate_artists(self, artist_count: int, artists_prefix: bool, weight_min: float, weight_max: float, weight_total: float, seed: int):
        """
        根据输入参数生成最终 prompt。所有随机过程均由 seed 控制，输入的 seed 将直接决定结果。
        """
        with self.lock:
            count = min(artist_count, len(self.artists))
            prefix = "artist:" if artists_prefix else ""
            rng = random.Random(seed)
            base = self._get_random_artists(count, rng)
            order_adjusted = self._adjust_top_artists(base.copy(), rng)
            prompt = self._generate_prompt(order_adjusted, rng, weight_min, weight_max, weight_total, prefix)
            return (prompt,)

NODE_CLASS_MAPPINGS = {"RollingArtist": RollingArtist}
NODE_DISPLAY_NAME_MAPPINGS = {"RollingArtist": "Rolling Artist"}