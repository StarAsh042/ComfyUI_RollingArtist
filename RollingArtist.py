import os
import random
import csv
import threading
import json
from typing import List, Tuple, Optional, Dict, Any, Union

class RollingArtist:
    """
    RollingArtist 节点类
    
    该类用于在ComfyUI中生成随机艺术家组合的提示词，支持从CSV文件中加载艺术家列表，
    并根据配置生成带有权重的艺术家提示词字符串。
    
    特性:
    - 支持从CSV文件加载艺术家列表
    - 可配置生成的艺术家数量和权重
    - 支持Top艺术家优先选择机制
    - 线程安全的生成过程
    - 可自定义权重分配算法
    """
    
    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        """
        定义节点的输入参数类型和界面显示
        
        返回:
            包含所有输入参数配置的字典
        """
        return {
            "required": {
                "artist_count": ("INT", {
                    "default": 3,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "description": "选择生成的艺术家人数（1-10）"
                }),
                "artist_top_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "slider",
                    "description": "输出中包含的Top艺术家数量（至少1个）"
                }),
                "artist_top_ratio": ("FLOAT", {
                    "default": 0.01,
                    "min": 0.01,
                    "max": 1.0,
                    "step": 0.01,
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
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "单个艺术家最小权重值"
                }),
                "weight_max": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.1,
                    "max": 2.0,
                    "step": 0.1,
                    "display": "slider",
                    "description": "单个艺术家最大权重值"
                }),
                "weight_total": ("FLOAT", {
                    "default": 2.0,
                    "min": 0.0,
                    "max": 20.0,
                    "step": 0.5,
                    "display": "slider",
                    "description": "所有权重值的总和"
                }),
                "seed": ("INT", {
                    "default": 1234,
                    "min": 0,
                    "max": 4294967295,
                    "description": "控制随机性的种子值"
                }),
            },
            "optional": {
                "custom_csv_path": ("STRING", {
                    "default": "",
                    "description": "自定义CSV路径，留空使用默认"
                }),
                "exclude_artists": ("STRING", {
                    "default": "",
                    "description": "排除的艺术家，逗号分隔"
                }),
                "sort_by_weight": ("BOOLEAN", {
                    "default": True,
                    "description": "是否按权重从高到低排序"
                }),
            }
        }

    # 定义节点的输出类型和名称
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "artists_json")
    FUNCTION = "generate_artists"  # 指定节点的主函数
    CATEGORY = "RollingArtist"    # 节点分类
    OUTPUT_NODE = True            # 标记为输出节点

    def __init__(self) -> None:
        """
        初始化RollingArtist实例
        
        - 加载艺术家列表
        - 初始化线程锁
        - 创建艺术家池
        - 设置默认的Top艺术家比例
        """
        self.artists = self.load_artists()  # 加载艺术家列表
        self.lock = threading.Lock()       # 创建线程锁，确保线程安全
        self.top_pool: List[str] = []      # 初始化Top艺术家池
        self.non_top_pool: List[str] = []  # 初始化非Top艺术家池
        self.update_top_pool(0.2)          # 使用默认比例更新艺术家池

    def load_artists(self, csv_path: Optional[str] = None) -> List[str]:
        """
        从CSV文件加载艺术家列表
        
        返回:
            艺术家名称列表，如果加载失败则返回空列表
        """
        csv_path = csv_path or os.path.join(os.path.dirname(__file__), "danbooru_art_001.csv")
        try:
            try:
                mtime = os.path.getmtime(csv_path)
            except Exception:
                mtime = None
            if getattr(self, "_last_csv_path", None) == csv_path and getattr(self, "_last_csv_mtime", None) == mtime and getattr(self, "artists", None):
                return self.artists
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                result: List[str] = []
                for row in reader:
                    for artist in row:
                        if artist:
                            result.append(artist)
                self.artists = result
                self._last_csv_path = csv_path
                self._last_csv_mtime = mtime
                return result
        except FileNotFoundError:
            print(f"[RollingArtist] CSV文件未找到: {csv_path}")
            return []
        except Exception as e:
            print(f"[RollingArtist] CSV加载失败: {str(e)}")
            return []

    def update_top_pool(self, top_ratio: float) -> None:
        """
        根据比例更新Top艺术家池和非Top艺术家池
        
        参数:
            top_ratio: 选取Top艺术家的比例(0.0-1.0)
        """
        if not self.artists:
            return
            
        # 确保计算结果为整数
        top_count = int(len(self.artists) * top_ratio)
        # 至少保留1个艺术家
        top_count = max(1, top_count)
        
        # 若比例未变且艺术家列表未变，跳过重算
        if getattr(self, "_last_top_ratio", None) == top_ratio and getattr(self, "_last_artists_id", None) == id(self.artists) and self.top_pool and self.non_top_pool:
            return
        self.top_pool = self.artists[:top_count]
        top_set = set(self.top_pool)
        self.non_top_pool = [a for a in self.artists if a not in top_set]
        self._last_top_ratio = top_ratio
        self._last_artists_id = id(self.artists)

    def generate_fixed_weights(self, count: int, weight_min: float, 
                              weight_max: float, weight_total: float, 
                              rng: random.Random) -> List[float]:
        """
        生成符合约束的权重列表
        
        该方法确保生成的权重列表满足以下条件:
        1. 每个权重值在weight_min和weight_max之间
        2. 所有权重的总和精确等于weight_total(在有效范围内)
        3. 权重分配具有随机性
        
        参数:
            count: 需要生成的权重数量
            weight_min: 单个权重的最小值
            weight_max: 单个权重的最大值
            weight_total: 期望的权重总和
            rng: 随机数生成器
            
        返回:
            生成的权重列表，每个值保留一位小数
        """
        n = count
        # 确保权重总和在有效范围内
        lower_bound = n * weight_min  # 最小可能的权重总和
        upper_bound = n * weight_max  # 最大可能的权重总和
        weight_total = max(lower_bound, min(weight_total, upper_bound))  # 调整权重总和到有效范围
        weight_total = round(weight_total, 1)  # 确保权重总和保留一位小数
        
        # 初始化所有权重为最小值
        weights = [weight_min] * n
        
        # 计算剩余可分配的权重总和
        remaining = round(weight_total - sum(weights), 1)
        
        # 创建索引列表并随机打乱，确保随机分配
        indices = list(range(n))
        rng.shuffle(indices)
        
        # 单次循环分配剩余权重
        for i in indices:
            if i == indices[-1]:  # 最后一个元素
                # 为最后一个元素分配所有剩余权重，确保总和精确匹配
                addition = remaining
                # 确保不超过最大权重限制
                if weights[i] + addition > weight_max:
                    addition = weight_max - weights[i]
                    # 如果最后一个元素无法容纳所有剩余权重，尝试分配给其他元素
                    extra = remaining - addition
                    if extra > 0:
                        for j in range(n):
                            if j != i and weights[j] < weight_max:
                                add_to_j = min(weight_max - weights[j], extra)
                                weights[j] += add_to_j
                                extra -= add_to_j
                                if extra <= 0:
                                    break
            else:
                # 计算当前权重可以增加的最大值
                max_add = min(weight_max - weights[i], remaining * 0.8)  # 留出20%给后续元素
                if max_add <= 0:
                    continue  # 没有剩余权重可分配
                    
                # 随机分配一个增量值
                addition = round(rng.uniform(0, max_add), 1)  # 随机增加的权重，保留一位小数
            
            weights[i] += addition
            remaining = round(remaining - addition, 1)  # 更新剩余可分配权重
            
            if remaining <= 0:
                break
        
        # 最终检查，确保权重总和精确等于目标值
        actual_total = round(sum(weights), 1)
        if actual_total != weight_total and n > 0:
            # 找到可以调整的元素
            for i in range(n):
                if actual_total < weight_total and weights[i] < weight_max:
                    weights[i] = round(weights[i] + (weight_total - actual_total), 1)
                    break
                elif actual_total > weight_total and weights[i] > weight_min:
                    weights[i] = round(weights[i] - (actual_total - weight_total), 1)
                    break
        
        # 返回最终权重列表，确保每个值保留一位小数
        return [round(w, 1) for w in weights]

    def generate_artists(self, artist_count: int, artist_top_count: int,
                        artist_top_ratio: float, artists_prefix: bool,
                        weight_min: float, weight_max: float,
                        weight_total: float, seed: int,
                        custom_csv_path: str = "", exclude_artists: str = "",
                        sort_by_weight: bool = True) -> Tuple[str, str]:
        """
        主生成函数，根据参数生成艺术家提示词
        
        该方法执行以下步骤:
        1. 更新艺术家池
        2. 选择指定数量的艺术家(包括Top艺术家和非Top艺术家)
        3. 为每个艺术家生成权重
        4. 构建最终的提示词字符串
        
        参数:
            artist_count: 要生成的艺术家总数
            artist_top_count: 要包含的Top艺术家数量
            artist_top_ratio: Top艺术家池的比例
            artists_prefix: 是否添加'artist:'前缀
            weight_min: 单个艺术家的最小权重
            weight_max: 单个艺术家的最大权重
            weight_total: 所有权重的总和
            seed: 随机数种子
            
        返回:
            包含生成的提示词字符串的元组
        """
        # 读取CSV不加锁，避免阻塞；仅在赋值时加锁
        if custom_csv_path:
            new_artists = self.load_artists(custom_csv_path)
            with self.lock:
                self.artists = new_artists
        with self.lock:
            if artist_count < 1 or not self.artists:
                return ("", "")
                
            # 更新Top艺术家池（根据CSV前百分比）
            self.update_top_pool(artist_top_ratio)

            exclude_set = set([a.strip() for a in exclude_artists.split(",") if a.strip()])
            available_top_pool = [a for a in self.top_pool if a not in exclude_set]
            available_non_top_pool = [a for a in self.non_top_pool if a not in exclude_set]
            
            # 计算实际要输出的Top数量（直接使用输入值）
            actual_top = max(1, min(
                artist_top_count,  # 用户指定的Top数量
                artist_count,      # 不能超过总艺术家数
                len(self.top_pool) # 不能超过可用的Top艺术家数
            ))
            
            # 初始化随机数生成器
            rng = random.Random(seed)
            
            # 确保至少选择1个Top艺术家
            selected_top = rng.sample(available_top_pool, min(actual_top, len(available_top_pool)))
            # 计算剩余需要的艺术家数量
            remaining = max(0, artist_count - len(selected_top))
            # 从非Top池中选择剩余数量的艺术家
            selected_non_top = rng.sample(available_non_top_pool, min(remaining, len(available_non_top_pool)))
            
            # 合并并随机打乱最终的艺术家列表
            final_order = selected_top + selected_non_top
            rng.shuffle(final_order)
            
            # 为选中的艺术家生成权重
            weights = self.generate_fixed_weights(
                len(final_order), weight_min, weight_max, weight_total, rng
            )

            if sort_by_weight and final_order:
                pairs = list(zip(final_order, weights))
                pairs.sort(key=lambda x: x[1], reverse=True)
                final_order, weights = [p[0] for p in pairs], [p[1] for p in pairs]
            
            # 构建prompt
            prefix = "artist:" if artists_prefix else ""
            prompt_parts = [
                (f"{prefix}{artist}" if weight == 1.0 else f"({prefix}{artist}:{weight})")
                for artist, weight in zip(final_order, weights)
            ]
            
            prompt = ",".join(prompt_parts)
            top_set = set(self.top_pool)
            data = {
                "artists": [
                    {
                        "name": artist,
                        "weight": weight,
                        "top": artist in top_set,
                    }
                    for artist, weight in zip(final_order, weights)
                ]
            }
            return (prompt, json.dumps(data, ensure_ascii=False))

# 注册节点类
NODE_CLASS_MAPPINGS = {"RollingArtist": RollingArtist}
# 设置节点显示名称
NODE_DISPLAY_NAME_MAPPINGS = {"RollingArtist": "Rolling Artist"}
