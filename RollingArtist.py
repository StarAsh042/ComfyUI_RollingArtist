import os
import random
import csv
import threading
from typing import List

class RollingArtist:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "artist_count": ("INT", {
                    "default": 5,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "description": "选择生成的艺术家人数（1-10）"
                }),
                "artist_top_count": ("INT", {
                    "default": 3,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "description": "输出中包含的Top艺术家数量（至少1个）"
                }),
                "artist_top_ratio": ("FLOAT", {
                    "default": 0.2,
                    "min": 0.1,
                    "max": 1.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "提取CSV中Top艺术家占前百分比,已知CSV越靠前艺术家作品越多"
                }),
                "artists_prefix": ("BOOLEAN", {
                    "default": True,
                    "description": "是否为艺术家名称添加'artist:'前缀"
                }),
                "weight_min": ("FLOAT", {
                    "default": 0.2,
                    "min": 0.1,
                    "max": 1.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "单个艺术家最小权重值"
                }),
                "weight_max": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.5,
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "单个艺术家最大权重值"
                }),
                "weight_total": ("FLOAT", {
                    "default": 3.0,
                    "min": 1.0,
                    "max": 20.0,
                    "step": 1.0,
                    "display": "slider",
                    "description": "所有权重值的总和"
                }),
                "seed": ("INT", {
                    "default": 1234,
                    "min": 0,
                    "max": 4294967295,
                    "description": "控制随机性的种子值"
                }),
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
        self.top_pool = []
        self.non_top_pool = []
        self.update_top_pool(0.2)

    def load_artists(self) -> List[str]:
        """加载固定的CSV文件"""
        csv_path = os.path.join(os.path.dirname(__file__), "danbooru_art_001.csv")
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                return [artist for row in reader for artist in row if artist]
        except FileNotFoundError:
            print(f"[RollingArtist] CSV文件未找到: {csv_path}")
            return []
        except Exception as e:
            print(f"[RollingArtist] CSV加载失败: {str(e)}")
            return []

    def update_top_pool(self, top_ratio: float):
        """根据比例更新Top艺术家池"""
        if not self.artists:
            return
            
        # 确保计算结果为整数
        top_count = int(len(self.artists) * top_ratio)
        # 至少保留1个艺术家
        top_count = max(1, top_count)
        
        self.top_pool = self.artists[:top_count]
        self.non_top_pool = [a for a in self.artists if a not in self.top_pool]

    def generate_fixed_weights(self, count: int, weight_min: float, 
                              weight_max: float, weight_total: float, 
                              rng: random.Random) -> List[float]:
        """生成符合约束的权重列表"""
        n = count
        lower_bound = n * weight_min
        upper_bound = n * weight_max
        weight_total = max(lower_bound, min(weight_total, upper_bound))
        
        extra_total = round(weight_total - lower_bound, 1)
        weights = [weight_min] * n
        
        # 第一阶段分配
        for i in range(n):
            allowed = min(weight_max - weight_min, extra_total)
            extra = round(rng.uniform(0, allowed), 1)
            weights[i] += extra
            extra_total = round(extra_total - extra, 1)
            if extra_total <= 0:
                break
                
        # 剩余分配
        if extra_total > 0:
            for i in range(n):
                can_add = weight_max - weights[i]
                addition = min(can_add, extra_total)
                weights[i] += addition
                extra_total = round(extra_total - addition, 1)
                if extra_total <= 0:
                    break
                    
        return [round(w, 1) for w in weights]

    def _get_random_order(self, count: int, rng: random.Random) -> List[str]:
        """获取随机排序的艺术家列表"""
        lst = self.artists.copy()
        rng.shuffle(lst)
        return lst[:count]

    def _adjust_top(self, artists: List[str], rng: random.Random) -> List[str]:
        """调整Top艺术家数量"""
        top_count = sum(1 for a in artists if a in self.top_pool)
        if top_count <= 3:
            return artists
            
        # 替换多余的Top艺术家
        replace_indices = [i for i, a in enumerate(artists) if a in self.top_pool]
        rng.shuffle(replace_indices)
        candidates = list(set(self.non_top_pool) - set(artists))
        
        for idx in replace_indices[:top_count-3]:
            if not candidates:
                break
            artists[idx] = rng.choice(candidates)
            candidates.remove(artists[idx])
            
        return artists

    def generate_artists(self, artist_count: int, artist_top_count: int,
                        artist_top_ratio: float, artists_prefix: bool,
                        weight_min: float, weight_max: float,
                        weight_total: float, seed: int):
        """主生成函数"""
        with self.lock:
            if artist_count < 1 or not self.artists:
                return ("",)
                
            # 更新Top艺术家池（根据CSV前百分比）
            self.update_top_pool(artist_top_ratio)
            
            # 计算实际要输出的Top数量（直接使用输入值）
            actual_top = max(1, min(
                artist_top_count, 
                artist_count, 
                len(self.top_pool)
            ))
            
            # 生成艺术家列表
            rng = random.Random(seed)
            
            # 确保至少选择1个Top艺术家
            selected_top = rng.sample(self.top_pool, min(actual_top, len(self.top_pool)))
            remaining = max(0, artist_count - len(selected_top))
            selected_non_top = rng.sample(self.non_top_pool, min(remaining, len(self.non_top_pool)))
            
            final_order = selected_top + selected_non_top
            rng.shuffle(final_order)
            
            # 生成权重
            weights = self.generate_fixed_weights(
                len(final_order), weight_min, weight_max, weight_total, rng
            )
            
            # 构建prompt
            prefix = "artist:" if artists_prefix else ""
            prompt_parts = [
                f"({prefix}{artist}:{weight})" 
                for artist, weight in zip(final_order, weights)
            ]
            
            return (",".join(prompt_parts),)

NODE_CLASS_MAPPINGS = {"RollingArtist": RollingArtist}
NODE_DISPLAY_NAME_MAPPINGS = {"RollingArtist": "Rolling Artist"}